from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime
from pathlib import Path
from textwrap import wrap
from typing import Any
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

README_PATH = ROOT / "README.md"
JSON_PATH = ROOT / "data" / "insight.json"
SVG_PATH = ROOT / "assets" / "ai-tech-insight.svg"

TIMEZONE = ZoneInfo("Asia/Dhaka")

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PRIMARY_MODEL = os.getenv(
    "GEMINI_PRIMARY_MODEL",
    "gemini-3.8-flash",
).strip()

FALLBACK_MODEL = os.getenv(
    "GEMINI_FALLBACK_MODEL",
    "gemini-3.5-flash-lite",
).strip()

MODELS = list(dict.fromkeys([PRIMARY_MODEL, FALLBACK_MODEL]))

MAX_ATTEMPTS_PER_MODEL = 2
REQUEST_TIMEOUT = 45
INITIAL_RETRY_DELAY = 3
MAX_RETRY_DELAY = 20

MAX_OUTPUT_TOKENS = 1200

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

AUTHOR = "Muhammad Junaeid"

CATEGORIES = {
    "Software Engineering",
    "Cybersecurity",
    "Artificial Intelligence",
    "Cloud Computing",
    "Web Development",
    "Programming Languages",
    "DevOps",
    "Data Engineering",
    "Computer Science",
    "Emerging Technology",
    "Networking",
    "Developer Tools",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

LOGGER = logging.getLogger("tech-insight")


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are the editorial research engine for an independent,
professional technology journal.

Create one original, technically accurate technology insight.

The audience includes software engineers, cybersecurity
professionals, developers, students and technology enthusiasts.

Requirements:

1. Choose a specific, meaningful technical concept.
2. Avoid generic motivational content.
3. Explain the concept accurately in approximately 45-75 words.
4. Explain the actual mechanism, not just its benefits.
5. Write one practical, technically responsible takeaway.
6. Avoid unsupported performance claims.
7. Do not repeat an ordinary introductory topic unnecessarily.
8. Use professional, clear English.
9. Do not invent standards, vulnerabilities, APIs or research.
10. Select exactly one category from the allowed categories.

Return ONLY a valid JSON object with these fields:

{
  "title": "Technical concept",
  "category": "Allowed category",
  "explanation": "Technical explanation",
  "takeaway": "Practical takeaway"
}

Do not include Markdown fences or additional commentary.
"""


# ============================================================
# EXCEPTIONS
# ============================================================

class InsightError(Exception):
    """Base application exception."""


class TemporaryAPIError(InsightError):
    """A retryable API failure."""


class PermanentAPIError(InsightError):
    """A non-retryable API failure."""


class ValidationError(InsightError):
    """Invalid generated content."""


# ============================================================
# TIME
# ============================================================

def current_time() -> datetime:
    return datetime.now(TIMEZONE)


def iso_timestamp() -> str:
    return current_time().isoformat(timespec="seconds")


def display_timestamp() -> str:
    return current_time().strftime("%Y-%m-%d %H:%M:%S %z")


# ============================================================
# GEMINI REST CLIENT
# ============================================================

def extract_api_error(body: bytes) -> str:
    try:
        parsed = json.loads(body.decode("utf-8"))
        return str(
            parsed.get("error", {}).get(
                "message",
                "Unknown API error",
            )
        )[:700]
    except (ValueError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")[:700]


def request_gemini(model: str) -> str:
    encoded_model = urllib.parse.quote(model, safe="-.")

    url = (
        f"{API_BASE}/{encoded_model}:generateContent"
        f"?key={urllib.parse.quote(API_KEY, safe='')}"
    )

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Generate one fresh technology insight "
                            "for today's publication. "
                            f"Current publication date: {iso_timestamp()}"
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "category": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                    "takeaway": {"type": "STRING"},
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
            },
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        },
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MuhammadJunaeid-TechInsight/2.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as exc:
        message = extract_api_error(exc.read())

        if exc.code in (408, 409, 425, 429, 500, 502, 503, 504):
            raise TemporaryAPIError(
                f"HTTP {exc.code}: {message}"
            ) from exc

        raise PermanentAPIError(
            f"HTTP {exc.code}: {message}"
        ) from exc

    except (
        urllib.error.URLError,
        TimeoutError,
        ConnectionError,
    ) as exc:
        raise TemporaryAPIError(
            f"Network error: {exc}"
        ) from exc

    candidates = result.get("candidates", [])

    if not candidates:
        raise ValidationError(
            "Gemini returned no candidates."
        )

    candidate = candidates[0]

    if candidate.get("finishReason") == "MAX_TOKENS":
        raise ValidationError(
            "Gemini output was truncated."
        )

    parts = candidate.get("content", {}).get("parts", [])

    text_parts = [
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
    ]

    output = "\n".join(text_parts).strip()

    if not output:
        raise ValidationError(
            "Gemini returned empty text."
        )

    return output


# ============================================================
# MODEL FAILOVER AND RETRIES
# ============================================================

def generate_raw_content() -> str:
    failures = []

    for model_index, model in enumerate(MODELS):
        LOGGER.info(
            "Using model %s (%d/%d)",
            model,
            model_index + 1,
            len(MODELS),
        )

        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                LOGGER.info(
                    "Generation attempt %d/%d",
                    attempt,
                    MAX_ATTEMPTS_PER_MODEL,
                )

                return request_gemini(model)

            except TemporaryAPIError as exc:
                failures.append(f"{model}: {exc}")

                LOGGER.warning(
                    "%s temporarily unavailable: %s",
                    model,
                    exc,
                )

                if attempt < MAX_ATTEMPTS_PER_MODEL:
                    delay = min(
                        INITIAL_RETRY_DELAY * (2 ** (attempt - 1)),
                        MAX_RETRY_DELAY,
                    )

                    delay += random.uniform(0, min(2, delay * 0.25))

                    LOGGER.info(
                        "Retrying after %.1f seconds",
                        delay,
                    )

                    time.sleep(delay)

            except (PermanentAPIError, ValidationError) as exc:
                LOGGER.error(
                    "Non-retryable failure: %s",
                    exc,
                )
                raise

        if model_index + 1 < len(MODELS):
            LOGGER.warning(
                "Primary model exhausted. Switching to %s.",
                MODELS[model_index + 1],
            )

    raise InsightError(
        "All configured Gemini models failed. "
        + " | ".join(failures)
    )


# ============================================================
# CONTENT VALIDATION
# ============================================================

def clean_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{field} must be a string."
        )

    value = re.sub(r"\s+", " ", value).strip()

    if not value:
        raise ValidationError(
            f"{field} cannot be empty."
        )

    if len(value) > 1200:
        raise ValidationError(
            f"{field} is too long."
        )

    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValidationError(
            f"{field} contains invalid control characters."
        )

    return value


def validate_insight(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Invalid JSON response: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValidationError(
            "Insight must be a JSON object."
        )

    required = {
        "title",
        "category",
        "explanation",
        "takeaway",
    }

    if set(data.keys()) != required:
        raise ValidationError(
            "Unexpected or missing JSON fields."
        )

    insight = {
        key: clean_string(data[key], key)
        for key in required
    }

    if insight["category"] not in CATEGORIES:
        raise ValidationError(
            f"Unsupported category: {insight['category']}"
        )

    if len(insight["title"]) > 100:
        raise ValidationError("Title exceeds 100 characters.")

    if len(insight["explanation"]) < 100:
        raise ValidationError(
            "Explanation is too short."
        )

    if len(insight["explanation"]) > 750:
        raise ValidationError(
            "Explanation is too long."
        )

    if len(insight["takeaway"]) < 30:
        raise ValidationError(
            "Takeaway is too short."
        )

    if len(insight["takeaway"]) > 300:
        raise ValidationError(
            "Takeaway is too long."
        )

    if re.search(r"<\s*script|javascript\s*:", raw, re.I):
        raise ValidationError(
            "Potentially unsafe generated content."
        )

    insight["date"] = iso_timestamp()

    return insight


# ============================================================
# SVG GENERATION
# ============================================================

def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def wrap_text(
    value: str,
    width: int,
    max_lines: int,
) -> list[str]:
    lines = wrap(
        value,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )

    if len(lines) > max_lines:
        raise ValidationError(
            "SVG text exceeds its available layout."
        )

    return lines


def svg_text(
    lines: list[str],
    x: int,
    y: int,
    font_size: int,
    color: str,
    weight: int = 400,
    line_height: int | None = None,
) -> str:
    line_height = line_height or int(font_size * 1.45)

    elements = []

    for index, line in enumerate(lines):
        position = y + index * line_height

        elements.append(
            f'<text x="{x}" y="{position}" '
            f'font-size="{font_size}" '
            f'font-weight="{weight}" '
            f'fill="{color}">'
            f'{svg_escape(line)}</text>'
        )

    return "\n".join(elements)


def generate_svg(insight: dict[str, str]) -> str:
    title = wrap_text(insight["title"], 34, 2)
    explanation = wrap_text(
        insight["explanation"],
        112,
        6,
    )
    takeaway = wrap_text(
        insight["takeaway"],
        88,
        2,
    )

    date = current_time().strftime(
        "%Y-%m-%d %H:%M:%S +06"
    )

    title_svg = svg_text(
        title, 220, 500, 112, "#FFFFFF", 750, 135
    )

    explanation_svg = svg_text(
        explanation, 220, 760, 43,
        "#D8E3F5", 400, 67
    )

    takeaway_svg = svg_text(
        takeaway, 280, 1280, 42,
        "#FFFFFF", 650, 65
    )

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="3840" height="2160"
     viewBox="0 0 3840 2160"
     role="img"
     aria-labelledby="svg-title svg-desc">

<title id="svg-title">{svg_escape(insight["title"])}</title>
<desc id="svg-desc">{svg_escape(insight["explanation"])}</desc>

<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#080C18"/>
    <stop offset="55%" stop-color="#111B31"/>
    <stop offset="100%" stop-color="#17102C"/>
  </linearGradient>

  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#D879F2"/>
    <stop offset="50%" stop-color="#F6BD69"/>
    <stop offset="100%" stop-color="#66E5B2"/>
  </linearGradient>

  <radialGradient id="glow">
    <stop offset="0%" stop-color="#9C69D9" stop-opacity=".35"/>
    <stop offset="100%" stop-color="#080C18" stop-opacity="0"/>
  </radialGradient>

  <pattern id="grid" width="60" height="60"
           patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="2"
            fill="#A5B4FC" opacity=".15"/>
  </pattern>
</defs>

<rect width="3840" height="2160" fill="url(#background)"/>
<rect width="3840" height="2160" fill="url(#grid)"/>

<circle cx="3100" cy="850" r="1150" fill="url(#glow)"/>
<circle cx="3450" cy="300" r="750" fill="url(#glow)"/>

<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="220" y="205" font-size="56"
        font-weight="800" letter-spacing="5"
        fill="#FFFFFF">
    JUNAEID <tspan fill="#D879F2">/</tspan> TECH JOURNAL
  </text>

  <rect x="220" y="275" width="10" height="55"
        rx="5" fill="url(#accent)"/>

  <text x="260" y="315" font-size="35"
        font-weight="750" letter-spacing="4"
        fill="#D879F2">
    {svg_escape(insight["category"].upper())}
  </text>

  {title_svg}

  <line x1="220" y1="620" x2="2150" y2="620"
        stroke="#94A3B8" stroke-opacity=".3"
        stroke-width="3"/>

  {explanation_svg}

  <rect x="220" y="1130" width="1950" height="260"
        rx="30" fill="#FFFFFF" fill-opacity=".055"
        stroke="#FFFFFF" stroke-opacity=".15"
        stroke-width="2"/>

  <rect x="220" y="1130" width="10" height="260"
        rx="5" fill="url(#accent)"/>

  <text x="280" y="1215" font-size="34"
        font-weight="800" letter-spacing="4"
        fill="#D879F2">
    KEY TAKEAWAY
  </text>

  {takeaway_svg}

  <g transform="translate(2520 500)">
    <circle cx="500" cy="500" r="470"
            fill="none" stroke="url(#accent)"
            stroke-width="3" opacity=".35"/>

    <circle cx="500" cy="500" r="360"
            fill="none" stroke="#A5B4FC"
            stroke-width="2" stroke-dasharray="12 25"
            opacity=".5"/>

    <circle cx="500" cy="500" r="235"
            fill="#FFFFFF" fill-opacity=".025"
            stroke="url(#accent)" stroke-width="4"/>

    <path d="M330 500 L445 615 L680 355"
          fill="none" stroke="url(#accent)"
          stroke-width="24" stroke-linecap="round"
          stroke-linejoin="round"/>

    <circle cx="500" cy="500" r="510"
            fill="none" stroke="#D879F2"
            stroke-width="2" stroke-dasharray="4 35"
            opacity=".5"/>
  </g>

  <line x1="220" y1="2010" x2="3610" y2="2010"
        stroke="#94A3B8" stroke-opacity=".25"
        stroke-width="2"/>

  <text x="220" y="2070" font-size="30"
        font-weight="750" letter-spacing="2"
        fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="3610" y="2070" font-size="26"
        text-anchor="end" fill="#A5B4FC">
    {date} · DHAKA
  </text>

  <text x="3610" y="2120" font-size="22"
        text-anchor="end" letter-spacing="4"
        fill="#D879F2">
    DIGITAL INTELLIGENCE JOURNAL
  </text>

</g>
</svg>
'''


# ============================================================
# README PUBLICATION
# ============================================================

def generate_readme_block(insight: dict[str, str]) -> str:
    title = insight["title"]
    category = insight["category"]
    explanation = insight["explanation"]
    takeaway = insight["takeaway"]
    date = insight["date"]

    return f"""<div align="center">

### AUTOMATED TECH INSIGHT OF THE DAY

# {title}

`{category}` · `{date}`

![Junaeid Tech Journal](assets/ai-tech-insight.svg)

</div>

**The insight**

{explanation}

> **KEY TAKEAWAY**
>
> {takeaway}

<sub>Created by Muhammad Junaeid · AI-assisted technology journal</sub>"""


def update_readme(existing: str, block: str) -> str:
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

    if end <= start:
        raise ValidationError(
            "README publication markers are incorrectly ordered."
        )

    before = existing[:start + len(START_MARKER)]
    after = existing[end:]

    return (
        before
        + "\n\n"
        + block
        + "\n\n"
        + after
    )


# ============================================================
# FILE VALIDATION
# ============================================================

def validate_svg(svg: str) -> None:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValidationError(
            f"Generated SVG is invalid: {exc}"
        ) from exc

    if not root.tag.endswith("svg"):
        raise ValidationError("Invalid SVG root.")

    if root.attrib.get("viewBox") != "0 0 3840 2160":
        raise ValidationError("Unexpected SVG dimensions.")

    if not root.find("{http://www.w3.org/2000/svg}title") is not None:
        raise ValidationError("SVG title is missing.")

    if not root.find("{http://www.w3.org/2000/svg}desc") is not None:
        raise ValidationError("SVG description is missing.")

    forbidden = {"script", "foreignObject", "iframe"}

    for element in root.iter():
        name = element.tag.split("}")[-1]

        if name in forbidden:
            raise ValidationError(
                f"Forbidden SVG element: {name}"
            )

        for key, value in element.attrib.items():
            if key.lower().startswith("on"):
                raise ValidationError(
                    "SVG event handlers are forbidden."
                )

            if "javascript:" in value.lower():
                raise ValidationError(
                    "Unsafe SVG attribute detected."
                )


# ============================================================
# ATOMIC FILE WRITES
# ============================================================

def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)

            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, path)

    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    LOGGER.info("=" * 60)
    LOGGER.info("AUTOMATED TECH INSIGHT")
    LOGGER.info("Created by %s", AUTHOR)
    LOGGER.info("=" * 60)

    if not API_KEY:
        LOGGER.error(
            "GEMINI_API_KEY is missing. Configure the GitHub secret."
        )
        return 1

    if not README_PATH.is_file():
        LOGGER.error("README.md does not exist.")
        return 1

    try:
        existing_readme = README_PATH.read_text(
            encoding="utf-8"
        )

        if not existing_readme.strip():
            raise ValidationError("README.md is empty.")

        # Generate and validate before changing any files.
        raw = generate_raw_content()
        insight = validate_insight(raw)

        LOGGER.info("Generated: %s", insight["title"])
        LOGGER.info("Category: %s", insight["category"])

        svg = generate_svg(insight)
        validate_svg(svg)

        readme = update_readme(
            existing_readme,
            generate_readme_block(insight),
        )

        json_output = json.dumps(
            insight,
            indent=2,
            ensure_ascii=False,
        ) + "\n"

        # Final consistency checks before publication.
        parsed_json = json.loads(json_output)

        if parsed_json != insight:
            raise ValidationError(
                "JSON serialization validation failed."
            )

        if START_MARKER not in readme or END_MARKER not in readme:
            raise ValidationError(
                "README markers missing after generation."
            )

        # Publish only after all generation and validation succeed.
        atomic_write(JSON_PATH, json_output)
        atomic_write(SVG_PATH, svg)
        atomic_write(README_PATH, readme)

        LOGGER.info("=" * 60)
        LOGGER.info("PUBLICATION SUCCESSFUL")
        LOGGER.info("JSON: %s", JSON_PATH)
        LOGGER.info("SVG: %s", SVG_PATH)
        LOGGER.info("README: %s", README_PATH)
        LOGGER.info("Model configuration: %s", ", ".join(MODELS))
        LOGGER.info("=" * 60)

        return 0

    except Exception as exc:
        LOGGER.error(
            "TECH INSIGHT GENERATION FAILED: %s",
            exc,
        )
        LOGGER.error(
            "Existing publication was not intentionally cleared."
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
