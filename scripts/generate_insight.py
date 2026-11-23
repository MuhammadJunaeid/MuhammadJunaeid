from __future__ import annotations
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime
from pathlib import Path
from textwrap import wrap
from zoneinfo import ZoneInfo
from html import escape


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

README_FILE = ROOT / "README.md"
JSON_FILE = ROOT / "data" / "insight.json"
SVG_FILE = ROOT / "assets" / "ai-tech-insight.svg"

TIMEZONE = ZoneInfo("Asia/Dhaka")

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

PRIMARY_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
).strip()

FALLBACK_MODEL = os.getenv(
    "GEMINI_FALLBACK_MODEL",
    "gemini-3.5-flash-lite",
).strip()

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

REQUEST_TIMEOUT = 90
MAX_ATTEMPTS_PER_MODEL = 2

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

AUTHOR = "Muhammad Junaeid"

ALLOWED_CATEGORIES = [
    "Artificial Intelligence",
    "Web Development",
    "Cybersecurity",
    "Software Engineering",
    "Systems & Architecture",
    "Emerging Technology",
    "Developer Productivity",
    "Cloud Computing",
    "Data & Privacy",
]

CATEGORY_ALIASES = {
    "ai": "Artificial Intelligence",
    "artificial intelligence": "Artificial Intelligence",
    "machine learning": "Artificial Intelligence",
    "web": "Web Development",
    "web development": "Web Development",
    "cyber security": "Cybersecurity",
    "cybersecurity": "Cybersecurity",
    "software engineering": "Software Engineering",
    "software development": "Software Engineering",
    "systems and architecture": "Systems & Architecture",
    "systems & architecture": "Systems & Architecture",
    "systems architecture": "Systems & Architecture",
    "system architecture": "Systems & Architecture",
    "architecture": "Systems & Architecture",
    "emerging technology": "Emerging Technology",
    "emerging technologies": "Emerging Technology",
    "developer productivity": "Developer Productivity",
    "cloud": "Cloud Computing",
    "cloud computing": "Cloud Computing",
    "data and privacy": "Data & Privacy",
    "data & privacy": "Data & Privacy",
    "data privacy": "Data & Privacy",
}

JSON_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {
            "type": "STRING",
            "description": "A concise and technically accurate insight title."
        },
        "category": {
            "type": "STRING",
            "enum": ALLOWED_CATEGORIES
        },
        "explanation": {
            "type": "STRING",
            "description": (
                "An accurate, self-contained technical explanation "
                "of approximately 60 to 100 words."
            )
        },
        "takeaway": {
            "type": "STRING",
            "description": (
                "One practical and technically responsible takeaway."
            )
        },
    },
    "required": [
        "title",
        "category",
        "explanation",
        "takeaway",
    ],
    "propertyOrdering": [
        "title",
        "category",
        "explanation",
        "takeaway",
    ],
}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("automated-tech-insight")


# ============================================================
# EXCEPTIONS
# ============================================================

class GeminiError(Exception):
    pass


class QuotaExceededError(GeminiError):
    pass


class TemporaryAPIError(GeminiError):
    pass


class PermanentAPIError(GeminiError):
    pass


class ValidationError(Exception):
    pass


# ============================================================
# GENERAL UTILITIES
# ============================================================

def normalize_category(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("Category must be a string.")

    normalized = " ".join(value.strip().split())
    lookup = normalized.casefold()

    category = CATEGORY_ALIASES.get(lookup)

    if category:
        return category

    for allowed in ALLOWED_CATEGORIES:
        if normalized.casefold() == allowed.casefold():
            return allowed

    raise ValidationError(
        f"Unsupported category: {value}"
    )


def clean_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{field} must be a string."
        )

    result = " ".join(value.split()).strip()

    if not result:
        raise ValidationError(
            f"{field} cannot be empty."
        )

    return result


def validate_insight(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValidationError(
            "Generated response must be a JSON object."
        )

    required = {
        "title",
        "category",
        "explanation",
        "takeaway",
    }

    missing = required - data.keys()

    if missing:
        raise ValidationError(
            f"Missing fields: {', '.join(sorted(missing))}"
        )

    result = {
        "title": clean_text(data["title"], "title"),
        "category": normalize_category(data["category"]),
        "explanation": clean_text(
            data["explanation"], "explanation"
        ),
        "takeaway": clean_text(
            data["takeaway"], "takeaway"
        ),
    }

    if len(result["title"]) > 120:
        raise ValidationError("Title is too long.")

    if len(result["explanation"]) < 100:
        raise ValidationError(
            "Explanation is too short."
        )

    if len(result["explanation"]) > 1000:
        raise ValidationError(
            "Explanation is too long."
        )

    if len(result["takeaway"]) > 300:
        raise ValidationError(
            "Takeaway is too long."
        )

    return result


# ============================================================
# GEMINI REST API
# ============================================================

def build_prompt() -> str:
    categories = ", ".join(ALLOWED_CATEGORIES)

    return f"""
You are the editorial technology researcher for
Junaeid Tech Journal, created by {AUTHOR}.

Generate ONE original, technically accurate technology insight.

Editorial requirements:

1. Choose a meaningful software engineering, cybersecurity,
   computing, developer or emerging technology concept.

2. Avoid generic motivational content.

3. Explain the mechanism, not merely its definition.

4. Use technically precise language understandable to
   intermediate developers.

5. The title must be concise.

6. The explanation must contain approximately 60-100 words.

7. The takeaway must offer a practical and responsible lesson.
   Do not make unsupported performance or security claims.

8. Do not invent benchmarks, statistics, CVEs or citations.

9. Avoid presenting an experimental feature as universally
   available or enabled by default.

10. Select exactly one category from this list:

{categories}

Return only JSON matching the supplied schema.
""".strip()


def parse_retry_after(headers) -> float | None:
    value = headers.get("Retry-After")

    if not value:
        return None

    try:
        return max(0.0, min(float(value), 120.0))
    except (TypeError, ValueError):
        return None


def request_gemini(model: str) -> dict:
    endpoint = (
        f"{API_BASE}/{urllib.parse.quote(model, safe='')}"
        ":generateContent?"
        f"{urllib.parse.urlencode({'key': API_KEY})}"
    )

    payload = {
        "systemInstruction": {
            "parts": [{
                "text": (
                    "You are a precise technology editor. "
                    "Return valid structured JSON only."
                )
            }]
        },
        "contents": [{
            "role": "user",
            "parts": [{
                "text": build_prompt()
            }]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": JSON_SCHEMA,
            "temperature": 0.8,
            "maxOutputTokens": 1200,
        },
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            raw = response.read().decode("utf-8")

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8", errors="replace"
        )

        message = error_body.lower()

        if error.code == 429:
            if any(term in message for term in (
                "per_day",
                "per day",
                "daily",
                "quota exceeded",
                "free_tier_requests",
            )):
                raise QuotaExceededError(
                    f"Daily quota exhausted for {model}."
                )

            raise TemporaryAPIError(
                f"HTTP 429: {error_body[:500]}"
            )

        if error.code in (408, 500, 502, 503, 504):
            raise TemporaryAPIError(
                f"HTTP {error.code}: {error_body[:500]}"
            )

        if error.code in (401, 403):
            raise PermanentAPIError(
                f"Authentication or permission error: "
                f"HTTP {error.code}: {error_body[:500]}"
            )

        raise PermanentAPIError(
            f"HTTP {error.code}: {error_body[:500]}"
        )

    except urllib.error.URLError as error:
        raise TemporaryAPIError(
            f"Network error: {error.reason}"
        ) from error

    try:
        response_data = json.loads(raw)

        candidates = response_data.get("candidates", [])

        if not candidates:
            raise ValidationError(
                "Gemini returned no candidates."
            )

        parts = candidates[0].get(
            "content", {}
        ).get("parts", [])

        generated_text = "".join(
            part.get("text", "")
            for part in parts
        ).strip()

        if not generated_text:
            raise ValidationError(
                "Gemini returned empty content."
            )

        generated = json.loads(generated_text)

        return validate_insight(generated)

    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValidationError(
            f"Invalid Gemini response: {error}"
        ) from error


def generate_insight() -> dict:
    if not API_KEY:
        raise PermanentAPIError(
            "GEMINI_API_KEY is missing."
        )

    models = []

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        if model and model not in models:
            models.append(model)

    if not models:
        raise PermanentAPIError(
            "No Gemini models configured."
        )

    last_error = None

    for model_index, model in enumerate(models):
        logger.info(
            "Using model %s (%s/%s)",
            model,
            model_index + 1,
            len(models),
        )

        for attempt in range(
            1, MAX_ATTEMPTS_PER_MODEL + 1
        ):
            logger.info(
                "Generation attempt %s/%s",
                attempt,
                MAX_ATTEMPTS_PER_MODEL,
            )

            try:
                return request_gemini(model)

            except QuotaExceededError as error:
                logger.warning(
                    "Daily quota exhausted: %s",
                    error,
                )
                last_error = error
                break

            except TemporaryAPIError as error:
                logger.warning(
                    "Temporary API failure: %s",
                    error,
                )

                last_error = error

                if attempt < MAX_ATTEMPTS_PER_MODEL:
                    delay = min(
                        2 ** attempt + random.uniform(0, 1),
                        15,
                    )

                    logger.info(
                        "Retrying after %.1f seconds",
                        delay,
                    )
                    time.sleep(delay)

            except (
                PermanentAPIError,
                ValidationError,
            ):
                raise

    raise GeminiError(
        "All configured Gemini models failed. "
        f"Last error: {last_error}"
    )


# ============================================================
# PUBLICATION CONTENT
# ============================================================

def get_timestamp() -> datetime:
    return datetime.now(TIMEZONE)


def build_json(data: dict, timestamp: datetime) -> str:
    output = {
        **data,
        "date": timestamp.isoformat(timespec="seconds"),
        "author": AUTHOR,
        "generator": "Junaeid Tech Journal",
    }

    return json.dumps(
        output,
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def build_readme_section(
    data: dict,
    timestamp: datetime,
) -> str:
    date_text = timestamp.strftime(
        "%Y-%m-%d %H:%M:%S %z"
    )

    cache_key = timestamp.strftime(
        "%Y%m%d%H%M%S"
    )

    return f"""<div align="center">

### AUTOMATED TECH INSIGHT OF THE DAY

**{data["title"]}**

`{data["category"]}` · `{date_text}`

![Junaeid Tech Journal](assets/ai-tech-insight.svg?v={cache_key})

</div>

**The insight**

{data["explanation"]}

> **KEY TAKEAWAY**
>
> {data["takeaway"]}

<sub>Created by {AUTHOR} · AI-assisted technology journal</sub>"""


def update_readme(existing: str, section: str) -> str:
    if existing.count(START_MARKER) != 1:
        raise ValidationError(
            "README must contain exactly one INSIGHT:START marker."
        )

    if existing.count(END_MARKER) != 1:
        raise ValidationError(
            "README must contain exactly one INSIGHT:END marker."
        )

    start = existing.index(START_MARKER)
    end = existing.index(END_MARKER)

    if start >= end:
        raise ValidationError(
            "README publication markers are incorrectly ordered."
        )

    before = existing[:start + len(START_MARKER)]
    after = existing[end:]

    return (
        before
        + "\n\n"
        + section
        + "\n\n"
        + after
    )


# ============================================================
# SVG GENERATION
# ============================================================

def wrap_svg_text(
    value: str,
    max_chars: int,
) -> list[str]:
    return wrap(
        value,
        width=max_chars,
        break_long_words=False,
        break_on_hyphens=False,
    )


def svg_text(
    x: int,
    y: int,
    value: str,
    size: int,
    color: str,
    weight: int = 400,
) -> str:
    return (
        f'<text x="{x}" y="{y}" '
        f'font-size="{size}" '
        f'font-weight="{weight}" '
        f'fill="{color}">'
        f'{escape(value)}'
        f'</text>'
    )


def build_svg(
    data: dict,
    timestamp: datetime,
) -> str:
    title = data["title"]
    category = data["category"]
    explanation = data["explanation"]
    takeaway = data["takeaway"]

    explanation_lines = wrap_svg_text(
        explanation, 83
    )[:7]

    takeaway_lines = wrap_svg_text(
        takeaway, 76
    )[:3]

    explanation_svg = "\n".join(
        svg_text(
            220,
            690 + index * 76,
            line,
            43,
            "#D8E3F5",
        )
        for index, line in enumerate(
            explanation_lines
        )
    )

    takeaway_svg = "\n".join(
        svg_text(
            285,
            1280 + index * 70,
            line,
            39,
            "#FFFFFF",
            600,
        )
        for index, line in enumerate(
            takeaway_lines
        )
    )

    safe_title = escape(title)
    safe_category = escape(category)

    date_label = timestamp.strftime(
        "%Y-%m-%d %H:%M:%S +06"
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="3840"
     height="2160"
     viewBox="0 0 3840 2160"
     role="img"
     aria-labelledby="svg-title svg-desc">

<title id="svg-title">{safe_title}</title>
<desc id="svg-desc">{escape(explanation)}</desc>

<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#080C18"/>
    <stop offset="55%" stop-color="#111B31"/>
    <stop offset="100%" stop-color="#17102C"/>
  </linearGradient>

  <linearGradient id="accent">
    <stop offset="0%" stop-color="#F472B6"/>
    <stop offset="50%" stop-color="#FBBF24"/>
    <stop offset="100%" stop-color="#4ADE80"/>
  </linearGradient>

  <radialGradient id="glow">
    <stop offset="0%" stop-color="#C084FC" stop-opacity=".3"/>
    <stop offset="100%" stop-color="#080C18" stop-opacity="0"/>
  </radialGradient>

  <pattern id="grid" width="64" height="64"
           patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="2"
            fill="#A5B4FC" opacity=".16"/>
  </pattern>
</defs>

<rect width="3840" height="2160" fill="url(#background)"/>
<rect width="3840" height="2160" fill="url(#grid)"/>

<circle cx="3150" cy="900" r="1100" fill="url(#glow)"/>
<circle cx="3500" cy="200" r="850" fill="url(#glow)"/>

<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="220" y="205"
        font-size="58"
        font-weight="800"
        letter-spacing="6"
        fill="#FFFFFF">
    JUNAEID
    <tspan fill="#F472B6"> / </tspan>
    TECH JOURNAL
  </text>

  <rect x="220" y="280" width="10" height="55"
        rx="5" fill="url(#accent)"/>

  <text x="260" y="322"
        font-size="38"
        font-weight="750"
        letter-spacing="4"
        fill="#F472B6">
    {safe_category.upper()}
  </text>

  <text x="220" y="500"
        font-size="100"
        font-weight="800"
        fill="#FFFFFF">
    {safe_title}
  </text>

  <line x1="220" y1="565" x2="2200" y2="565"
        stroke="#94A3B8" opacity=".3" stroke-width="3"/>

  <text x="220" y="625"
        font-size="34"
        font-weight="700"
        letter-spacing="3"
        fill="#FBBF24">
    THE INSIGHT
  </text>

  {explanation_svg}

  <rect x="220" y="1100"
        width="1950" height="340"
        rx="32"
        fill="#FFFFFF"
        fill-opacity=".055"
        stroke="#FFFFFF"
        stroke-opacity=".14"
        stroke-width="2"/>

  <rect x="220" y="1100"
        width="10" height="340"
        rx="5" fill="url(#accent)"/>

  <text x="285" y="1190"
        font-size="34"
        font-weight="800"
        letter-spacing="4"
        fill="#F472B6">
    KEY TAKEAWAY
  </text>

  {takeaway_svg}

  <g opacity=".7">
    <circle cx="2920" cy="900" r="510"
            fill="none" stroke="url(#accent)"
            stroke-width="4" stroke-dasharray="12 24"/>
    <circle cx="2920" cy="900" r="390"
            fill="none" stroke="#A5B4FC"
            stroke-width="3" opacity=".4"/>
    <circle cx="2920" cy="900" r="250"
            fill="none" stroke="url(#accent)"
            stroke-width="8" opacity=".55"/>
    <path d="M2520 900 Q2920 420 3320 900
             Q2920 1380 2520 900"
          fill="none" stroke="#F472B6"
          stroke-width="5" opacity=".65"/>
    <path d="M2920 390 L2920 1410
             M2410 900 L3430 900"
          stroke="#A5B4FC" stroke-width="3"
          opacity=".35"/>
    <circle cx="2920" cy="900" r="22"
            fill="#FBBF24"/>
    <circle cx="2920" cy="900" r="60"
            fill="none" stroke="#FBBF24"
            stroke-width="3" opacity=".65"/>
  </g>

  <line x1="220" y1="1990"
        x2="3610" y2="1990"
        stroke="#94A3B8" opacity=".3"
        stroke-width="2"/>

  <text x="220" y="2060"
        font-size="30"
        font-weight="750"
        letter-spacing="3"
        fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="3610" y="2060"
        font-size="27"
        text-anchor="end"
        fill="#A5B4FC">
    {date_label} · DHAKA
  </text>

  <text x="3610" y="2110"
        font-size="23"
        text-anchor="end"
        letter-spacing="4"
        fill="#F472B6">
    DIGITAL INTELLIGENCE JOURNAL
  </text>

</g>
</svg>
"""


# ============================================================
# VALIDATION
# ============================================================

def validate_svg(content: str) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValidationError(
            f"Invalid SVG/XML: {error}"
        ) from error

    if not root.tag.endswith("svg"):
        raise ValidationError(
            "Generated artwork is not an SVG."
        )

    if root.attrib.get("width") != "3840":
        raise ValidationError(
            "SVG width must be 3840."
        )

    if root.attrib.get("height") != "2160":
        raise ValidationError(
            "SVG height must be 2160."
        )

    if root.attrib.get("viewBox") != "0 0 3840 2160":
        raise ValidationError(
            "SVG viewBox is invalid."
        )


def validate_json(content: str) -> None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"Invalid publication JSON: {error}"
        ) from error

    validate_insight(parsed)


# ============================================================
# SAFE PUBLICATION
# ============================================================

def publish_files(files: dict[Path, str]) -> None:
    """
    Prepare all files before publication.
    Keep original bytes for rollback if a write fails.
    """

    originals = {}
    temporary_files = {}

    try:
        for path, content in files.items():
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            originals[path] = (
                path.read_bytes()
                if path.exists()
                else None
            )

            temp_path = path.with_name(
                path.name + ".tmp"
            )

            temp_path.write_text(
                content,
                encoding="utf-8",
            )

            temporary_files[path] = temp_path

        for path, temp_path in temporary_files.items():
            os.replace(temp_path, path)

    except Exception:
        logger.exception(
            "Publication write failed. Rolling back."
        )

        for path, original in originals.items():
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    rollback_path = path.with_name(
                        path.name + ".rollback"
                    )

                    rollback_path.write_bytes(original)
                    os.replace(rollback_path, path)

            except Exception:
                logger.critical(
                    "Rollback failed for %s",
                    path,
                    exc_info=True,
                )

        raise

    finally:
        for temp_path in temporary_files.values():
            temp_path.unlink(missing_ok=True)


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    logger.info("=" * 60)
    logger.info("AUTOMATED TECH INSIGHT")
    logger.info("Created by %s", AUTHOR)
    logger.info("=" * 60)

    try:
        if not README_FILE.exists():
            raise ValidationError(
                "README.md was not found."
            )

        existing_readme = README_FILE.read_text(
            encoding="utf-8"
        )

        timestamp = get_timestamp()

        generated = generate_insight()

        logger.info(
            "Generated insight: %s",
            generated["title"],
        )

        json_content = build_json(
            generated,
            timestamp,
        )

        svg_content = build_svg(
            generated,
            timestamp,
        )

        section = build_readme_section(
            generated,
            timestamp,
        )

        readme_content = update_readme(
            existing_readme,
            section,
        )

        # Validate everything before touching live files.
        validate_json(json_content)
        validate_svg(svg_content)

        if not readme_content.strip():
            raise ValidationError(
                "Generated README is empty."
            )

        files = {
            JSON_FILE: json_content,
            SVG_FILE: svg_content,
            README_FILE: readme_content,
        }

        publish_files(files)

        logger.info("=" * 60)
        logger.info("PUBLICATION SUCCESSFUL")
        logger.info("Title: %s", generated["title"])
        logger.info("Category: %s", generated["category"])
        logger.info("Files updated: 3")
        logger.info("=" * 60)

        return 0

    except Exception:
        logger.exception(
            "TECH INSIGHT GENERATION FAILED."
        )

        logger.error(
            "Existing publication was not intentionally cleared."
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
