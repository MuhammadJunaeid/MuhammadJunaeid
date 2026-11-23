from __future__ import annotations
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from textwrap import wrap
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
README_FILE = ROOT / "README.md"
JSON_FILE = ROOT / "data" / "insight.json"
SVG_FILE = ROOT / "assets" / "ai-tech-insight.svg"
TIMEZONE = ZoneInfo("Asia/Dhaka")
AUTHOR = "Muhammad Junaeid"
GENERATOR = "Junaeid Tech Journal"

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
FALLBACK_MODELS = [
    item.strip()
    for item in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.5-flash-lite,gemini-2.5-flash-lite",
    ).split(",")
    if item.strip()
]
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT = max(10, int(os.getenv("GEMINI_TIMEOUT", "75")))
MAX_ATTEMPTS = max(1, min(4, int(os.getenv("GEMINI_MAX_ATTEMPTS", "2"))))
RETRY_BASE_SECONDS = max(1.0, float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "3")))
MAX_RETRY_WAIT = 45.0

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

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
    "ml": "Artificial Intelligence",
    "web": "Web Development",
    "web development": "Web Development",
    "frontend development": "Web Development",
    "front end development": "Web Development",
    "backend development": "Web Development",
    "back end development": "Web Development",
    "cyber security": "Cybersecurity",
    "cybersecurity": "Cybersecurity",
    "information security": "Cybersecurity",
    "software engineering": "Software Engineering",
    "software development": "Software Engineering",
    "systems and architecture": "Systems & Architecture",
    "systems architecture": "Systems & Architecture",
    "system architecture": "Systems & Architecture",
    "systems architecture design": "Systems & Architecture",
    "architecture": "Systems & Architecture",
    "emerging technology": "Emerging Technology",
    "emerging technologies": "Emerging Technology",
    "developer productivity": "Developer Productivity",
    "developer tools": "Developer Productivity",
    "cloud": "Cloud Computing",
    "cloud computing": "Cloud Computing",
    "data and privacy": "Data & Privacy",
    "data privacy": "Data & Privacy",
    "privacy": "Data & Privacy",
}

JSON_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "category": {"type": "STRING", "enum": ALLOWED_CATEGORIES},
        "explanation": {"type": "STRING"},
        "takeaway": {"type": "STRING"},
    },
    "required": ["title", "category", "explanation", "takeaway"],
    "propertyOrdering": ["title", "category", "explanation", "takeaway"],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("automated-tech-insight")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InsightError(Exception):
    """Base class for expected generation/publication errors."""


class QuotaExceededError(InsightError):
    pass


class TemporaryAPIError(InsightError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentAPIError(InsightError):
    pass


class ValidationError(InsightError):
    pass


# ---------------------------------------------------------------------------
# Validation and normalization
# ---------------------------------------------------------------------------

def _category_key(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def infer_category(title: str, explanation: str, takeaway: str) -> str:
    """Safely map an unexpected category to a relevant supported category."""
    text = f"{title} {explanation} {takeaway}".casefold()
    rules = [
        ("Cybersecurity", ("security", "vulnerability", "attack", "threat", "encryption", "authentication", "authorization", "malware", "ebpf")),
        ("Artificial Intelligence", ("artificial intelligence", "machine learning", "llm", "neural", "generative ai", "model inference")),
        ("Cloud Computing", ("cloud", "serverless", "kubernetes", "container orchestration", "distributed infrastructure")),
        ("Data & Privacy", ("privacy", "personal data", "data protection", "anonymization", "consent")),
        ("Web Development", ("browser", "frontend", "front-end", "backend", "web application", "http", "javascript", "css", "html")),
        ("Developer Productivity", ("developer tool", "debugging", "ide", "code editor", "workflow", "productivity")),
        ("Systems & Architecture", ("kernel", "operating system", "architecture", "microservice", "memory management", "concurrency", "protocol", "database engine")),
        ("Software Engineering", ("software", "testing", "api", "design pattern", "refactoring", "version control", "code quality")),
    ]
    for category, keywords in rules:
        if any(word in text for word in keywords):
            return category
    return "Emerging Technology"


def normalize_category(value: Any, context: dict[str, str] | None = None) -> str:
    if isinstance(value, str):
        key = _category_key(value)
        aliases = {_category_key(k): v for k, v in CATEGORY_ALIASES.items()}
        aliases.update({_category_key(item): item for item in ALLOWED_CATEGORIES})
        if key in aliases:
            return aliases[key]
    logger.warning("Unrecognized category %r; inferring from insight content.", value)
    context = context or {}
    return infer_category(
        context.get("title", ""),
        context.get("explanation", ""),
        context.get("takeaway", ""),
    )


def clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string.")
    # Remove control characters while retaining normal Unicode text.
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32)
    result = " ".join(value.split()).strip()
    if not result:
        raise ValidationError(f"{field} cannot be empty.")
    return result


def validate_insight(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValidationError("Generated response must be a JSON object.")
    title = clean_text(data.get("title"), "title")
    explanation = clean_text(data.get("explanation"), "explanation")
    takeaway = clean_text(data.get("takeaway"), "takeaway")
    category = normalize_category(
        data.get("category"),
        {"title": title, "explanation": explanation, "takeaway": takeaway},
    )
    if len(title) > 180:
        raise ValidationError("Title exceeds 180 characters.")
    if not 80 <= len(explanation) <= 1400:
        raise ValidationError("Explanation must contain 80-1400 characters.")
    if len(takeaway) > 500:
        raise ValidationError("Takeaway exceeds 500 characters.")
    return {"title": title, "category": category, "explanation": explanation, "takeaway": takeaway}


def extract_json(text: str) -> dict[str, Any]:
    """Accept JSON directly or safely extract a JSON object from code fences."""
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s*```$", "", candidate)
    try:
        result = json.loads(candidate)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            result = json.loads(candidate[start:end + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    raise ValidationError("Gemini response did not contain a valid JSON object.")


# ---------------------------------------------------------------------------
# Gemini REST API (no google-genai SDK required)
# ---------------------------------------------------------------------------

def build_prompt() -> str:
    return f"""You are the senior technology editor for {GENERATOR}, created by {AUTHOR}.

Create ONE original, useful, technically accurate technology insight.

Editorial standards:
- Focus on a concrete mechanism, engineering decision, security concept, or emerging technology.
- Explain how it works and why it matters; avoid generic motivational writing.
- Write for intermediate developers in clear, precise English.
- Title: concise and specific, ideally 4-10 words.
- Explanation: approximately 90-150 words, self-contained and technically responsible.
- Takeaway: one practical, actionable lesson.
- Do not invent benchmarks, statistics, CVEs, citations, or product capabilities.
- Do not claim a tool or feature is universally available or enabled by default.
- Avoid repeating a common topic with a superficial title; choose a meaningful angle.

Choose exactly one category from this list:
{', '.join(ALLOWED_CATEGORIES)}

Return only a JSON object with title, category, explanation, and takeaway.""".strip()


def _retry_after_seconds(headers) -> float | None:
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        return max(0.0, min(float(value), MAX_RETRY_WAIT))
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, min((date - datetime.now(timezone.utc)).total_seconds(), MAX_RETRY_WAIT))
        except Exception:
            return None


def _error_message(body: str) -> str:
    try:
        parsed = json.loads(body)
        error = parsed.get("error", {})
        return str(error.get("message") or body)
    except Exception:
        return body


def request_gemini(model: str) -> dict[str, str]:
    endpoint = f"{API_BASE}/{urllib.parse.quote(model, safe='')}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": "Return accurate structured JSON only. Do not include markdown fences."}]},
        "contents": [{"role": "user", "parts": [{"text": build_prompt()}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": JSON_SCHEMA,
            "temperature": 0.75,
            "maxOutputTokens": 1400,
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-goog-api-key": API_KEY,
            "User-Agent": "Junaeid-Tech-Journal/2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = _error_message(body)
        lower = f"{body} {message}".casefold()
        if exc.code == 429:
            if any(term in lower for term in ("per_day", "per day", "daily", "quota exceeded", "free_tier_requests", "requests per day")):
                raise QuotaExceededError(f"Daily quota exhausted for {model}: {message}") from exc
            raise TemporaryAPIError(f"HTTP 429 from {model}: {message}", _retry_after_seconds(exc.headers)) from exc
        if exc.code in (408, 500, 502, 503, 504):
            raise TemporaryAPIError(f"HTTP {exc.code} from {model}: {message}", _retry_after_seconds(exc.headers)) from exc
        if exc.code in (401, 403):
            raise PermanentAPIError(f"Gemini authentication/permission error (HTTP {exc.code}): {message}") from exc
        if exc.code == 404:
            raise PermanentAPIError(f"Gemini model {model!r} was not found or is unavailable: {message}") from exc
        raise PermanentAPIError(f"Gemini API rejected the request (HTTP {exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise TemporaryAPIError(f"Network error while contacting Gemini: {exc.reason}") from exc
    except TimeoutError as exc:
        raise TemporaryAPIError("Gemini request timed out.") from exc

    try:
        candidates = response_data.get("candidates") or []
        if not candidates:
            feedback = response_data.get("promptFeedback", {})
            raise ValidationError(f"Gemini returned no candidate. Feedback: {feedback}")
        parts = candidates[0].get("content", {}).get("parts", [])
        generated_text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if not generated_text:
            finish = candidates[0].get("finishReason", "unknown")
            raise ValidationError(f"Gemini returned empty content (finish reason: {finish}).")
        return validate_insight(extract_json(generated_text))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValidationError(f"Unexpected Gemini response structure: {exc}") from exc


def generate_insight() -> dict[str, str]:
    if not API_KEY:
        raise PermanentAPIError("GEMINI_API_KEY is missing. Add it as a GitHub Actions secret.")
    models = list(dict.fromkeys([m for m in [PRIMARY_MODEL, *FALLBACK_MODELS] if m]))
    if not models:
        raise PermanentAPIError("No Gemini models are configured.")

    failures: list[str] = []
    for model_index, model in enumerate(models, start=1):
        logger.info("Using model %s (%d/%d)", model, model_index, len(models))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info("Generation attempt %d/%d", attempt, MAX_ATTEMPTS)
            try:
                result = request_gemini(model)
                logger.info("Insight generated successfully with %s.", model)
                return result
            except QuotaExceededError as exc:
                logger.warning("%s", exc)
                failures.append(str(exc))
                # A daily quota will not recover by retrying this model today.
                break
            except TemporaryAPIError as exc:
                failures.append(str(exc))
                logger.warning("Temporary failure: %s", exc)
                if attempt < MAX_ATTEMPTS:
                    exponential = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                    delay = min(exc.retry_after if exc.retry_after is not None else exponential + random.uniform(0, 1.5), MAX_RETRY_WAIT)
                    logger.info("Retrying in %.1f seconds.", delay)
                    time.sleep(delay)
            except (PermanentAPIError, ValidationError):
                raise
    detail = " | ".join(failures[-6:]) or "No model returned a result."
    raise InsightError(
        "All configured Gemini models failed. The existing publication was preserved. "
        "Check model availability, quota, and API billing/limits. Details: " + detail
    )


# ---------------------------------------------------------------------------
# Publication content
# ---------------------------------------------------------------------------

def get_timestamp() -> datetime:
    return datetime.now(TIMEZONE)


def build_json(data: dict[str, str], timestamp: datetime) -> str:
    output = {
        **data,
        "date": timestamp.isoformat(timespec="seconds"),
        "author": AUTHOR,
        "generator": GENERATOR,
    }
    return json.dumps(output, ensure_ascii=False, indent=2) + "\n"


def build_readme_section(data: dict[str, str], timestamp: datetime) -> str:
    date_text = timestamp.strftime("%Y-%m-%d %H:%M:%S %z")
    cache_key = timestamp.strftime("%Y%m%d%H%M%S")
    return f'''<div align="center">

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

<sub>Created by {AUTHOR} · AI-assisted technology journal</sub>'''


def update_readme(existing: str, section: str) -> str:
    if existing.count(START_MARKER) != 1 or existing.count(END_MARKER) != 1:
        raise ValidationError("README.md must contain exactly one INSIGHT:START and one INSIGHT:END marker.")
    start = existing.index(START_MARKER)
    end = existing.index(END_MARKER)
    if start >= end:
        raise ValidationError("README publication markers are incorrectly ordered.")
    return existing[:start + len(START_MARKER)] + "\n\n" + section + "\n\n" + existing[end:]


# ---------------------------------------------------------------------------
# SVG generation and strict XML safety
# ---------------------------------------------------------------------------

def xml_safe_text(value: str) -> str:
    """Escape XML metacharacters and strip characters forbidden by XML 1.0."""
    valid = []
    for char in str(value):
        code = ord(char)
        if code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF:
            valid.append(char)
    return escape("".join(valid), quote=True)


def wrap_svg_text(value: str, max_chars: int) -> list[str]:
    return wrap(value, width=max_chars, break_long_words=True, break_on_hyphens=True) or [""]


def svg_text(x: int, y: int, value: str, size: int, color: str, weight: int = 400) -> str:
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{xml_safe_text(value)}</text>')


def build_svg(data: dict[str, str], timestamp: datetime) -> str:
    # Keep all content inside the left editorial column. Truncate only at word
    # boundaries and preserve the complete text in insight.json and README.
    title_lines = wrap_svg_text(data["title"], 34)[:2]
    explanation_lines = wrap_svg_text(data["explanation"], 76)[:8]
    takeaway_lines = wrap_svg_text(data["takeaway"], 68)[:3]

    title_svg = "\n".join(svg_text(220, 485 + i * 112, line, 88 if len(title_lines) == 1 else 76, "#FFFFFF", 800) for i, line in enumerate(title_lines))
    explanation_svg = "\n".join(svg_text(220, 690 + i * 68, line, 39, "#D8E3F5") for i, line in enumerate(explanation_lines))
    takeaway_svg = "\n".join(svg_text(285, 1280 + i * 66, line, 36, "#FFFFFF", 600) for i, line in enumerate(takeaway_lines))
    category = xml_safe_text(data["category"].upper())
    date_label = timestamp.strftime("%Y-%m-%d %H:%M:%S +06")

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="3840" height="2160" viewBox="0 0 3840 2160" role="img" aria-labelledby="svg-title svg-desc">
<title id="svg-title">{xml_safe_text(data["title"])}</title>
<desc id="svg-desc">{xml_safe_text(data["explanation"])}</desc>
<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#080C18"/><stop offset="55%" stop-color="#111B31"/><stop offset="100%" stop-color="#17102C"/></linearGradient>
  <linearGradient id="accent"><stop offset="0%" stop-color="#F472B6"/><stop offset="50%" stop-color="#FBBF24"/><stop offset="100%" stop-color="#4ADE80"/></linearGradient>
  <radialGradient id="glow"><stop offset="0%" stop-color="#C084FC" stop-opacity=".3"/><stop offset="100%" stop-color="#080C18" stop-opacity="0"/></radialGradient>
  <pattern id="grid" width="64" height="64" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="2" fill="#A5B4FC" opacity=".16"/></pattern>
</defs>
<rect width="3840" height="2160" fill="url(#background)"/><rect width="3840" height="2160" fill="url(#grid)"/>
<circle cx="3150" cy="900" r="1100" fill="url(#glow)"/><circle cx="3500" cy="200" r="850" fill="url(#glow)"/>
<g font-family="Inter, Segoe UI, Arial, sans-serif">
  <text x="220" y="205" font-size="58" font-weight="800" letter-spacing="6" fill="#FFFFFF">JUNAEID<tspan fill="#F472B6"> / </tspan>TECH JOURNAL</text>
  <rect x="220" y="280" width="10" height="55" rx="5" fill="url(#accent)"/>
  <text x="260" y="322" font-size="38" font-weight="750" letter-spacing="4" fill="#F472B6">{category}</text>
  {title_svg}
  <line x1="220" y1="565" x2="2200" y2="565" stroke="#94A3B8" opacity=".3" stroke-width="3"/>
  <text x="220" y="625" font-size="34" font-weight="700" letter-spacing="3" fill="#FBBF24">THE INSIGHT</text>
  {explanation_svg}
  <rect x="220" y="1100" width="1950" height="340" rx="32" fill="#FFFFFF" fill-opacity=".055" stroke="#FFFFFF" stroke-opacity=".14" stroke-width="2"/>
  <rect x="220" y="1100" width="10" height="340" rx="5" fill="url(#accent)"/>
  <text x="285" y="1190" font-size="34" font-weight="800" letter-spacing="4" fill="#F472B6">KEY TAKEAWAY</text>
  {takeaway_svg}
  <g opacity=".7"><circle cx="2920" cy="900" r="510" fill="none" stroke="url(#accent)" stroke-width="4" stroke-dasharray="12 24"/><circle cx="2920" cy="900" r="390" fill="none" stroke="#A5B4FC" stroke-width="3" opacity=".4"/><circle cx="2920" cy="900" r="250" fill="none" stroke="url(#accent)" stroke-width="8" opacity=".55"/><path d="M2520 900 Q2920 420 3320 900 Q2920 1380 2520 900" fill="none" stroke="#F472B6" stroke-width="5" opacity=".65"/><path d="M2920 390 L2920 1410 M2410 900 L3430 900" stroke="#A5B4FC" stroke-width="3" opacity=".35"/><circle cx="2920" cy="900" r="22" fill="#FBBF24"/><circle cx="2920" cy="900" r="60" fill="none" stroke="#FBBF24" stroke-width="3" opacity=".65"/></g>
  <line x1="220" y1="1990" x2="3610" y2="1990" stroke="#94A3B8" opacity=".3" stroke-width="2"/>
  <text x="220" y="2060" font-size="30" font-weight="750" letter-spacing="3" fill="#E2E8F0">CREATED BY MUHAMMAD JUNAEID</text>
  <text x="3610" y="2060" font-size="27" text-anchor="end" fill="#A5B4FC">{xml_safe_text(date_label)} · DHAKA</text>
  <text x="3610" y="2110" font-size="23" text-anchor="end" letter-spacing="4" fill="#F472B6">DIGITAL INTELLIGENCE JOURNAL</text>
</g>
</svg>
'''
    return svg


def validate_svg(content: str) -> None:
    # ElementTree rejects undefined XML entities, which is intentional. Our
    # generator emits escaped text rather than HTML-only entities such as nbsp.
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValidationError(f"Invalid SVG/XML: {exc}") from exc
    if not root.tag.endswith("svg"):
        raise ValidationError("Generated artwork is not an SVG document.")
    if root.attrib.get("width") != "3840" or root.attrib.get("height") != "2160":
        raise ValidationError("SVG dimensions must be 3840 x 2160.")
    if root.attrib.get("viewBox") != "0 0 3840 2160":
        raise ValidationError("SVG viewBox is invalid.")
    # Reject external references/scripts. This artwork should be self-contained.
    forbidden = ("<script", "javascript:", "http://", "https://", "<!ENTITY")
    lowered = content.casefold()
    if any(token in lowered for token in forbidden):
        # SVG namespace itself is the one expected http:// occurrence.
        stripped = lowered.replace('xmlns="http://www.w3.org/2000/svg"', "")
        if any(token in stripped for token in forbidden):
            raise ValidationError("SVG contains an unsafe external/script reference.")


def validate_json(content: str) -> None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid publication JSON: {exc}") from exc
    validate_insight(parsed)


# ---------------------------------------------------------------------------
# Safe publication with staging, backups, and rollback
# ---------------------------------------------------------------------------

def publish_files(files: dict[Path, str]) -> None:
    """Stage all files first, then replace them; restore originals on failure."""
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for target, content in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
            temp_path = Path(temp_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged[target] = temp_path
        for target, temp_path in staged.items():
            os.replace(temp_path, target)
            replaced.append(target)
    except Exception:
        logger.exception("Publication failed. Restoring previously published files.")
        for target in reversed(replaced):
            original = originals.get(target)
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    fd, backup_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".rollback", dir=str(target.parent))
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(backup_name, target)
            except Exception:
                logger.critical("Rollback failed for %s", target, exc_info=True)
        raise
    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logger.info("=" * 60)
    logger.info("AUTOMATED TECH INSIGHT")
    logger.info("Created by %s", AUTHOR)
    logger.info("=" * 60)
    try:
        if not README_FILE.is_file():
            raise ValidationError(f"README.md was not found at {README_FILE}.")
        existing_readme = README_FILE.read_text(encoding="utf-8")
        # Validate markers before spending an API request.
        if existing_readme.count(START_MARKER) != 1 or existing_readme.count(END_MARKER) != 1:
            raise ValidationError("README.md must contain exactly one pair of insight markers.")
        if existing_readme.index(START_MARKER) >= existing_readme.index(END_MARKER):
            raise ValidationError("README insight markers are incorrectly ordered.")

        timestamp = get_timestamp()
        generated = generate_insight()
        logger.info("Generated insight: %s", generated["title"])

        json_content = build_json(generated, timestamp)
        svg_content = build_svg(generated, timestamp)
        readme_content = update_readme(existing_readme, build_readme_section(generated, timestamp))

        # All validation occurs before any live publication file is replaced.
        validate_json(json_content)
        validate_svg(svg_content)
        if not readme_content.strip():
            raise ValidationError("Generated README content is empty.")

        publish_files({JSON_FILE: json_content, SVG_FILE: svg_content, README_FILE: readme_content})
        logger.info("=" * 60)
        logger.info("PUBLICATION SUCCESSFUL")
        logger.info("Title: %s", generated["title"])
        logger.info("Category: %s", generated["category"])
        logger.info("Files updated: 3")
        logger.info("=" * 60)
        return 0
    except Exception as exc:
        logger.error("TECH INSIGHT GENERATION FAILED: %s", exc)
        logger.error("Existing publication was not intentionally cleared or overwritten.")
        if logger.isEnabledFor(logging.DEBUG):
            logger.exception("Failure details")
        return 1


if __name__ == "__main__":
    sys.exit(main())
