from __future__ import annotations
import json
import logging
import math
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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from textwrap import wrap
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
README_FILE = ROOT / "README.md"
JSON_FILE = ROOT / "data" / "insight.json"
SVG_FILE = ROOT / "assets" / "ai-tech-insight.svg"
TIMEZONE = ZoneInfo("Asia/Dhaka")
AUTHOR = "Muhammad Junaeid"
JOURNAL = "JUNAEID / TECH JOURNAL"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
FALLBACK_MODELS = [x.strip() for x in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash-lite"
).split(",") if x.strip()]
REQUEST_TIMEOUT = max(10, int(os.getenv("GEMINI_TIMEOUT", "75")))
MAX_ATTEMPTS = max(1, min(3, int(os.getenv("GEMINI_MAX_ATTEMPTS", "2"))))
RETRY_BASE = max(1.0, float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "4")))
MAX_RETRY_WAIT = 35.0
START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

CATEGORIES = [
    "Artificial Intelligence", "Web Development", "Cybersecurity",
    "Software Engineering", "Systems & Architecture", "Emerging Technology",
    "Developer Productivity", "Cloud Computing", "Data & Privacy",
]
ALIASES = {
    "ai": "Artificial Intelligence", "machine learning": "Artificial Intelligence",
    "ml": "Artificial Intelligence", "artificial intelligence": "Artificial Intelligence",
    "web": "Web Development", "frontend development": "Web Development",
    "backend development": "Web Development", "web development": "Web Development",
    "cyber security": "Cybersecurity", "information security": "Cybersecurity",
    "security": "Cybersecurity", "software development": "Software Engineering",
    "software engineering": "Software Engineering", "systems architecture": "Systems & Architecture",
    "systems and architecture": "Systems & Architecture", "architecture": "Systems & Architecture",
    "cloud": "Cloud Computing", "cloud computing": "Cloud Computing",
    "privacy": "Data & Privacy", "data privacy": "Data & Privacy",
    "data and privacy": "Data & Privacy", "developer tools": "Developer Productivity",
}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "category": {"type": "STRING", "enum": CATEGORIES},
        "explanation": {"type": "STRING"},
        "takeaway": {"type": "STRING"},
    },
    "required": ["title", "category", "explanation", "takeaway"],
    "propertyOrdering": ["title", "category", "explanation", "takeaway"],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("junaeid-tech-journal")


class InsightError(Exception):
    pass


class QuotaError(InsightError):
    pass


class TemporaryAPIError(InsightError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentAPIError(InsightError):
    pass


class ValidationError(InsightError):
    pass


def compact_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold().replace("&", " and ")))


def infer_category(title: str, explanation: str, takeaway: str) -> str:
    text = f"{title} {explanation} {takeaway}".casefold()
    rules = [
        ("Cybersecurity", ("security", "vulnerability", "attack", "threat", "encryption", "authentication", "authorization", "malware", "firewall", "zero trust")),
        ("Artificial Intelligence", ("artificial intelligence", "machine learning", "llm", "neural", "generative ai", "inference", "embedding", "transformer")),
        ("Cloud Computing", ("cloud", "serverless", "kubernetes", "container orchestration", "cloud native")),
        ("Data & Privacy", ("privacy", "personal data", "data protection", "anonymization", "consent")),
        ("Web Development", ("browser", "frontend", "front-end", "backend", "web application", "http", "javascript", "css", "html")),
        ("Developer Productivity", ("developer tool", "debugging", "ide", "code editor", "workflow", "productivity")),
        ("Systems & Architecture", ("kernel", "operating system", "architecture", "microservice", "memory management", "concurrency", "protocol", "database", "connection pool", "queue")),
        ("Software Engineering", ("software", "testing", "api", "design pattern", "refactoring", "version control", "code quality")),
    ]
    for category, terms in rules:
        if any(term in text for term in terms):
            return category
    return "Emerging Technology"


def clean_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32)
    value = " ".join(value.split()).strip()
    if not value:
        raise ValidationError(f"{field} cannot be empty")
    return value


def validate_insight(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValidationError("Model response must be a JSON object")
    title = clean_text(raw.get("title"), "title")
    explanation = clean_text(raw.get("explanation"), "explanation")
    takeaway = clean_text(raw.get("takeaway"), "takeaway")
    category = raw.get("category", "")
    if isinstance(category, str):
        normalized = {compact_key(k): v for k, v in ALIASES.items()}
        normalized.update({compact_key(x): x for x in CATEGORIES})
        category = normalized.get(compact_key(category), "")
    if category not in CATEGORIES:
        log.warning("Unrecognized model category %r; inferring from content", raw.get("category"))
        category = infer_category(title, explanation, takeaway)
    if len(title) > 180:
        raise ValidationError("Title exceeds 180 characters")
    if not 80 <= len(explanation) <= 1800:
        raise ValidationError("Explanation must contain 80–1800 characters")
    if len(takeaway) > 650:
        raise ValidationError("Takeaway exceeds 650 characters")
    return {"title": title, "category": category, "explanation": explanation, "takeaway": takeaway}


def extract_json(text: str) -> dict[str, Any]:
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    left, right = candidate.find("{"), candidate.rfind("}")
    if left >= 0 and right > left:
        try:
            parsed = json.loads(candidate[left:right + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise ValidationError("Gemini did not return a valid JSON object")


def prompt() -> str:
    return f"""You are the senior technology editor of {JOURNAL}, created by {AUTHOR}.
Write one original, technically accurate insight for intermediate developers.
Choose a specific engineering mechanism, trade-off, security principle, or emerging technical idea. Explain what happens, why it matters, and a practical implication. Avoid generic advice, hype, fabricated metrics, invented CVEs, or unsupported product claims.
Title: precise, ideally 4–10 words. Explanation: 100–160 words, self-contained. Takeaway: one useful, actionable sentence.
Choose exactly one category: {', '.join(CATEGORIES)}.
Return only JSON with title, category, explanation, takeaway.""".strip()


def retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        return max(0.0, min(float(value), MAX_RETRY_WAIT))
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, min((date - datetime.now(timezone.utc)).total_seconds(), MAX_RETRY_WAIT))
        except Exception:
            return None


def api_error_message(body: str) -> str:
    try:
        return str(json.loads(body).get("error", {}).get("message") or body)
    except Exception:
        return body


def request_model(model: str) -> dict[str, str]:
    url = f"{API_BASE}/{urllib.parse.quote(model, safe='')}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": "Return valid structured JSON only."}]},
        "contents": [{"role": "user", "parts": [{"text": prompt()}]}],
        "generationConfig": {
            "responseMimeType": "application/json", "responseSchema": SCHEMA,
            "temperature": 0.78, "maxOutputTokens": 1600,
        },
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "x-goog-api-key": API_KEY, "User-Agent": "Junaeid-Tech-Journal/3.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = api_error_message(body)
        combined = f"{body} {message}".casefold()
        if exc.code == 429:
            if any(x in combined for x in ("per_day", "per day", "daily", "quota exceeded", "free_tier_requests", "requests per day")):
                raise QuotaError(f"Daily quota exhausted for {model}: {message}") from exc
            raise TemporaryAPIError(f"HTTP 429 from {model}: {message}", retry_after(exc.headers)) from exc
        if exc.code in (408, 500, 502, 503, 504):
            raise TemporaryAPIError(f"HTTP {exc.code} from {model}: {message}", retry_after(exc.headers)) from exc
        if exc.code in (401, 403):
            raise PermanentAPIError(f"Gemini authentication/permission error: {message}") from exc
        if exc.code == 404:
            raise PermanentAPIError(f"Model {model!r} is unavailable: {message}") from exc
        raise PermanentAPIError(f"Gemini rejected request (HTTP {exc.code}): {message}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TemporaryAPIError(f"Gemini network/timeout error: {exc}") from exc

    candidates = result.get("candidates") or []
    if not candidates:
        raise ValidationError(f"Gemini returned no candidate: {result.get('promptFeedback', {})}")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise ValidationError("Gemini returned an empty candidate")
    return validate_insight(extract_json(text))


def generate_insight() -> dict[str, str]:
    if not API_KEY:
        raise PermanentAPIError("GEMINI_API_KEY is missing; add it to GitHub Actions secrets")
    models = list(dict.fromkeys(x for x in [PRIMARY_MODEL, *FALLBACK_MODELS] if x))
    failures: list[str] = []
    for index, model in enumerate(models, 1):
        log.info("Model %s (%d/%d)", model, index, len(models))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                log.info("Generation attempt %d/%d", attempt, MAX_ATTEMPTS)
                return request_model(model)
            except QuotaError as exc:
                failures.append(str(exc))
                log.warning("%s; moving to next model", exc)
                break
            except TemporaryAPIError as exc:
                failures.append(str(exc))
                log.warning("Temporary model failure: %s", exc)
                if attempt < MAX_ATTEMPTS:
                    delay = exc.retry_after if exc.retry_after is not None else RETRY_BASE * (2 ** (attempt - 1)) + random.uniform(0.2, 1.4)
                    delay = min(max(0.0, delay), MAX_RETRY_WAIT)
                    log.info("Retrying in %.1f seconds", delay)
                    time.sleep(delay)
            except PermanentAPIError as exc:
                failures.append(str(exc))
                log.warning("Model unavailable: %s", exc)
                break
    raise InsightError("All configured Gemini models failed. Existing publication remains untouched. " + " | ".join(failures[-6:]))


def now_dhaka() -> datetime:
    return datetime.now(TIMEZONE)


def xml_text(value: str) -> str:
    valid = []
    for char in str(value):
        n = ord(char)
        if n in (9, 10, 13) or 32 <= n <= 0xD7FF or 0xE000 <= n <= 0xFFFD or 0x10000 <= n <= 0x10FFFF:
            valid.append(char)
    return escape("".join(valid), quote=True)


def wrap_text(value: str, max_chars: int) -> list[str]:
    # Do not discard words: every line is rendered into the artwork.
    return wrap(value, width=max_chars, break_long_words=True, break_on_hyphens=False) or [""]


def text_node(x: int, y: float, value: str, size: int, color: str, weight: int = 400, extra: str = "") -> str:
    return f'<text x="{x}" y="{y:.1f}" font-size="{size}" fill="{color}" font-weight="{weight}" {extra}>{xml_text(value)}</text>'


def choose_palette(seed: int) -> tuple[str, str, str, str]:
    palettes = [
        ("#8B5CF6", "#22D3EE", "#F472B6", "#F8FAFC"),
        ("#38BDF8", "#A3E635", "#FBBF24", "#F8FAFC"),
        ("#FB7185", "#C084FC", "#FDE68A", "#FFF7ED"),
        ("#34D399", "#60A5FA", "#F472B6", "#F0FDFA"),
        ("#F59E0B", "#F472B6", "#818CF8", "#FFFBEB"),
        ("#2DD4BF", "#818CF8", "#F9A8D4", "#F8FAFC"),
    ]
    return palettes[seed % len(palettes)]


def scene_kind(data: dict[str, str]) -> str:
    t = f"{data['title']} {data['explanation']}".casefold()
    groups = [
        ("database", ("database", "query", "connection pool", "sql", "transaction", "index")),
        ("network", ("network", "protocol", "tcp", "packet", "latency", "routing", "distributed")),
        ("security", ("security", "encryption", "authentication", "authorization", "threat", "vulnerability", "firewall", "privacy")),
        ("neural", ("neural", "machine learning", "model", "inference", "embedding", "transformer", "artificial intelligence", "generative")),
        ("cloud", ("cloud", "serverless", "kubernetes", "container", "orchestration", "scaling")),
        ("code", ("code", "compiler", "programming", "javascript", "python", "typescript", "debug", "testing", "api")),
        ("browser", ("browser", "frontend", "web", "css", "html", "rendering", "dom")),
    ]
    for kind, words in groups:
        if any(w in t for w in words):
            return kind
    return "systems"


def visual_scene(kind: str, a: str, b: str, c: str) -> str:
    # Each scene is an original, topic-relevant visual system, not a category icon.
    if kind == "database":
        return f'''<g class="hero-art">
          <ellipse cx="2940" cy="720" rx="470" ry="150" fill="none" stroke="{a}" stroke-width="8" opacity=".9"/>
          <path d="M2470 720 V1350 C2470 1435 2680 1500 2940 1500 S3410 1435 3410 1350 V720" fill="none" stroke="{b}" stroke-width="8"/>
          <path d="M2470 930 C2470 1015 2680 1080 2940 1080 S3410 1015 3410 930 M2470 1140 C2470 1225 2680 1290 2940 1290 S3410 1225 3410 1140" fill="none" stroke="{a}" stroke-width="5" opacity=".7"/>
          <g class="data-stream" fill="{c}"><circle cx="2600" cy="820" r="17"/><circle cx="3250" cy="1010" r="14"/><circle cx="2770" cy="1190" r="12"/><circle cx="3110" cy="1360" r="18"/></g>
          <path d="M2940 500 V1500 M2350 1080 H3530" stroke="{b}" stroke-width="2" opacity=".2" stroke-dasharray="12 20"/>
        </g>'''
    if kind == "security":
        return f'''<g class="hero-art"><path d="M2940 420 L3380 610 V960 C3380 1240 3190 1430 2940 1550 C2690 1430 2500 1240 2500 960 V610 Z" fill="{a}" fill-opacity=".08" stroke="{a}" stroke-width="9"/><path d="M2940 560 L3250 700 V955 C3250 1150 3120 1290 2940 1390 C2760 1290 2630 1150 2630 955 V700 Z" fill="none" stroke="{b}" stroke-width="5"/><path d="M2790 970 L2890 1070 L3110 830" fill="none" stroke="{c}" stroke-width="24" stroke-linecap="round" stroke-linejoin="round"/><circle cx="2940" cy="990" r="560" fill="none" stroke="{b}" stroke-dasharray="5 24" stroke-width="3" class="orbit"/></g>'''
    if kind == "neural":
        nodes = []
        for row, count in enumerate((4, 6, 5, 3)):
            x = 2510 + row * 280
            for j in range(count):
                y = 650 + j * (650 / max(1, count - 1)) + (row % 2) * 35
                nodes.append((x, y))
        lines = "".join(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{b}" stroke-width="3" opacity=".25"/>' for (x1,y1),(x2,y2) in zip(nodes, nodes[1:]))
        dots = "".join(f'<circle cx="{x}" cy="{y}" r="19" fill="{a}" class="node"/>' for x,y in nodes)
        return f'<g class="hero-art">{lines}{dots}<circle cx="2940" cy="1000" r="520" fill="none" stroke="{c}" stroke-width="3" stroke-dasharray="4 20" class="orbit"/></g>'
    if kind == "network":
        pts = [(2500,700),(2780,520),(3100,610),(3380,850),(3300,1220),(2980,1450),(2600,1260),(2800,950),(3160,980)]
        paths = "".join(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{b}" stroke-width="5" opacity=".45"/>' for i,(x1,y1) in enumerate(pts) for x2,y2 in pts[i+1:] if math.hypot(x2-x1,y2-y1)<620)
        dots = "".join(f'<circle cx="{x}" cy="{y}" r="25" fill="{a}" stroke="{c}" stroke-width="5" class="node"/>' for x,y in pts)
        return f'<g class="hero-art">{paths}{dots}<circle cx="2940" cy="990" r="610" fill="none" stroke="{c}" stroke-width="3" stroke-dasharray="8 24" class="orbit"/></g>'
    if kind == "cloud":
        return f'''<g class="hero-art"><path d="M2640 1200 C2420 1200 2390 900 2600 840 C2600 590 2910 500 3070 700 C3300 620 3480 820 3370 1010 C3540 1190 3380 1390 3190 1390 H2670 C2480 1390 2440 1240 2640 1200Z" fill="{a}" fill-opacity=".12" stroke="{a}" stroke-width="8"/><path d="M2940 790 V1260 M2740 1030 H3140 M2790 900 L3090 1190 M3090 900 L2790 1190" stroke="{b}" stroke-width="6" opacity=".7"/><circle cx="2940" cy="1030" r="80" fill="{c}" fill-opacity=".8" class="pulse"/></g>'''
    if kind == "code":
        return f'''<g class="hero-art"><rect x="2470" y="600" width="950" height="850" rx="42" fill="#101A31" stroke="{a}" stroke-width="7"/><path d="M2470 735 H3420" stroke="{b}" stroke-width="5"/><circle cx="2540" cy="670" r="14" fill="{c}"/><circle cx="2590" cy="670" r="14" fill="{a}"/><circle cx="2640" cy="670" r="14" fill="{b}"/><g fill="none" stroke-linecap="round" stroke-width="20"><path d="M2700 900 L2600 1010 L2700 1120" stroke="{a}"/><path d="M3190 900 L3290 1010 L3190 1120" stroke="{b}"/><path d="M3030 850 L2860 1170" stroke="{c}"/></g><path d="M2780 1280 H3150" stroke="{b}" stroke-width="9" stroke-dasharray="30 22" class="data-stream"/></g>'''
    if kind == "browser":
        return f'''<g class="hero-art"><rect x="2440" y="570" width="1000" height="900" rx="40" fill="#101A31" stroke="{a}" stroke-width="7"/><path d="M2440 720 H3440" stroke="{b}" stroke-width="5"/><circle cx="2520" cy="645" r="15" fill="{c}"/><circle cx="2580" cy="645" r="15" fill="{a}"/><circle cx="2640" cy="645" r="15" fill="{b}"/><rect x="2540" y="820" width="370" height="500" rx="20" fill="{a}" fill-opacity=".14" stroke="{a}" stroke-width="4"/><rect x="2960" y="820" width="360" height="210" rx="20" fill="{b}" fill-opacity=".13" stroke="{b}" stroke-width="4"/><rect x="2960" y="1080" width="360" height="240" rx="20" fill="{c}" fill-opacity=".12" stroke="{c}" stroke-width="4"/><path d="M2600 900 H2850 M2600 970 H2800 M2600 1040 H2820 M3020 900 H3260 M3020 1160 H3260" stroke="{c}" stroke-width="12" stroke-linecap="round" opacity=".8"/></g>'''
    return f'''<g class="hero-art"><circle cx="2940" cy="1000" r="500" fill="{a}" fill-opacity=".07" stroke="{a}" stroke-width="7"/><circle cx="2940" cy="1000" r="360" fill="none" stroke="{b}" stroke-width="5"/><circle cx="2940" cy="1000" r="210" fill="none" stroke="{c}" stroke-width="8"/><path d="M2440 1000 Q2940 420 3440 1000 Q2940 1580 2440 1000Z" fill="none" stroke="{b}" stroke-width="6"/><circle cx="2940" cy="1000" r="38" fill="{c}" class="pulse"/><path d="M2940 420 V1580 M2360 1000 H3520" stroke="{a}" stroke-width="3" opacity=".25"/></g>'''


def build_svg(data: dict[str, str], timestamp: datetime) -> str:
    seed = int(timestamp.strftime("%Y%m%d%H%M")) ^ sum(map(ord, data["title"]))
    rng = random.Random(seed)
    a, b, c, white = choose_palette(seed)
    kind = scene_kind(data)

    # Fixed editorial grid with measured line counts. The explanation is never sliced.
    title_lines = wrap_text(data["title"], 29)
    title_size = 104 if len(title_lines) <= 2 else 82
    title_leading = int(title_size * 1.16)
    title_start = 500
    title_svg = "\n".join(text_node(220, title_start + i * title_leading, line, title_size, white, 800) for i, line in enumerate(title_lines))

    expl_lines = wrap_text(data["explanation"], 66)
    expl_size = 43 if len(expl_lines) <= 8 else 38 if len(expl_lines) <= 10 else 34
    expl_leading = int(expl_size * 1.48)
    expl_start = title_start + len(title_lines) * title_leading + 150
    expl_end = expl_start + len(expl_lines) * expl_leading
    takeaway_lines = wrap_text(data["takeaway"], 58)
    takeaway_size = 39 if len(takeaway_lines) <= 3 else 34
    takeaway_leading = int(takeaway_size * 1.45)
    take_y = max(expl_end + 95, 1500)
    take_height = 110 + len(takeaway_lines) * takeaway_leading + 55
    if take_y + take_height > 1900:
        # Use a compact but readable type scale before considering a second page.
        expl_size = 31
        expl_leading = 44
        expl_start = title_start + len(title_lines) * title_leading + 115
        expl_end = expl_start + len(expl_lines) * expl_leading
        takeaway_size = 31
        takeaway_leading = 43
        take_y = max(expl_end + 55, 1500)
        take_height = 95 + len(takeaway_lines) * takeaway_leading + 45
    if take_y + take_height > 1930:
        raise ValidationError("Editorial text exceeds the safe 4K layout. Reduce generated explanation length.")

    explanation_svg = "\n".join(text_node(220, expl_start + i * expl_leading, line, expl_size, "#DCE7FA", 430) for i, line in enumerate(expl_lines))
    takeaway_svg = "\n".join(text_node(300, take_y + 120 + i * takeaway_leading, line, takeaway_size, white, 650) for i, line in enumerate(takeaway_lines))
    category = xml_text(data["category"].upper())
    date_label = timestamp.strftime("%d %b %Y  /  %H:%M:%S DHAKA")
    scene = visual_scene(kind, a, b, c)
    particles = "".join(
        f'<circle class="particle p{i}" cx="{rng.randint(2200,3650)}" cy="{rng.randint(250,1800)}" r="{rng.randint(3,9)}" fill="{rng.choice((a,b,c))}" opacity=".{rng.randint(25,70)}"/>'
        for i in range(34)
    )
    # The right-side visual has its own stage; editorial copy remains in a clear left column.
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="3840" height="2160" viewBox="0 0 3840 2160" role="img" aria-labelledby="art-title art-desc">
<title id="art-title">{xml_text(data['title'])} | {JOURNAL}</title>
<desc id="art-desc">{xml_text(data['explanation'])} Key takeaway: {xml_text(data['takeaway'])}</desc>
<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#070B16"/><stop offset=".52" stop-color="#111A31"/><stop offset="1" stop-color="#170F2B"/></linearGradient>
 <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{a}"/><stop offset=".52" stop-color="{b}"/><stop offset="1" stop-color="{c}"/></linearGradient>
 <radialGradient id="ambient"><stop offset="0" stop-color="{a}" stop-opacity=".25"/><stop offset="1" stop-color="#070B16" stop-opacity="0"/></radialGradient>
 <pattern id="microgrid" width="56" height="56" patternUnits="userSpaceOnUse"><path d="M56 0H0V56" fill="none" stroke="#A5B4FC" stroke-opacity=".055" stroke-width="1"/><circle cx="1" cy="1" r="2" fill="#C4B5FD" opacity=".12"/></pattern>
 <filter id="softGlow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="18" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
 <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%"><feGaussianBlur in="SourceAlpha" stdDeviation="18"/><feOffset dy="12"/><feComponentTransfer><feFuncA type="linear" slope=".28"/></feComponentTransfer><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
 <style>
  text{{font-family:Inter,'Segoe UI',Arial,sans-serif}}
  .particle{{animation:float 9s ease-in-out infinite alternate;transform-box:fill-box;transform-origin:center}}
  .p1,.p8,.p16,.p25{{animation-delay:-3s}} .p2,.p9,.p17,.p26{{animation-delay:-6s}}
  .hero-art{{transform-origin:2940px 1000px;animation:heroFloat 12s ease-in-out infinite alternate}}
  .orbit{{transform-origin:2940px 1000px;animation:orbit 48s linear infinite}}
  .pulse{{transform-origin:center;animation:pulse 3.8s ease-in-out infinite}}
  .node{{animation:nodeGlow 3s ease-in-out infinite alternate;filter:url(#softGlow)}}
  .data-stream{{animation:stream 5s ease-in-out infinite alternate;filter:url(#softGlow)}}
  .scan{{animation:scan 8s ease-in-out infinite}}
  @keyframes float{{from{{transform:translate3d(0,18px,0);opacity:.2}}to{{transform:translate3d(0,-28px,0);opacity:.8}}}}
  @keyframes heroFloat{{from{{transform:translateY(12px) scale(.99)}}to{{transform:translateY(-16px) scale(1.015)}}}}
  @keyframes orbit{{to{{transform:rotate(360deg)}}}}
  @keyframes pulse{{0%,100%{{opacity:.65;transform:scale(.88)}}50%{{opacity:1;transform:scale(1.12)}}}}
  @keyframes nodeGlow{{from{{opacity:.45}}to{{opacity:1}}}}
  @keyframes stream{{from{{opacity:.35}}to{{opacity:1}}}}
  @keyframes scan{{0%{{transform:translateY(0);opacity:0}}15%{{opacity:.45}}85%{{opacity:.15}}100%{{transform:translateY(700px);opacity:0}}}}
  @media (prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;transition:none!important}}}}
 </style>
</defs>
<rect width="3840" height="2160" fill="url(#bg)"/><rect width="3840" height="2160" fill="url(#microgrid)"/>
<circle cx="3000" cy="950" r="1300" fill="url(#ambient)"/><circle cx="3650" cy="180" r="700" fill="url(#ambient)" opacity=".65"/>
<path d="M2180 260V1870" stroke="#A5B4FC" stroke-opacity=".13" stroke-width="2" stroke-dasharray="4 18"/>
<g>{particles}</g>
<g font-family="Inter,'Segoe UI',Arial,sans-serif">
 <text x="220" y="205" font-size="58" font-weight="850" letter-spacing="5" fill="{white}">JUNAEID <tspan fill="{a}">/</tspan> TECH JOURNAL</text>
 <rect x="220" y="278" width="12" height="58" rx="6" fill="url(#accent)"/>
 <text x="265" y="321" font-size="36" font-weight="750" letter-spacing="4" fill="{c}">{category}</text>
 {title_svg}
 <rect x="220" y="{expl_start - 82}" width="270" height="48" rx="24" fill="{a}" fill-opacity=".13" stroke="{a}" stroke-opacity=".35"/>
 <text x="250" y="{expl_start - 49}" font-size="25" font-weight="750" letter-spacing="3" fill="{a}">THE INSIGHT</text>
 {explanation_svg}
 <g filter="url(#shadow)"><rect x="220" y="{take_y}" width="1850" height="{take_height}" rx="34" fill="#FFFFFF" fill-opacity=".065" stroke="#FFFFFF" stroke-opacity=".18" stroke-width="2"/><rect x="220" y="{take_y}" width="12" height="{take_height}" rx="6" fill="url(#accent)"/></g>
 <text x="300" y="{take_y + 70}" font-size="29" font-weight="850" letter-spacing="4" fill="{c}">KEY TAKEAWAY</text>
 {takeaway_svg}
 <text x="220" y="2040" font-size="28" font-weight="750" letter-spacing="3" fill="#DCE7FA">CREATED BY MUHAMMAD JUNAEID</text>
 <text x="3610" y="2040" font-size="25" text-anchor="end" letter-spacing="2" fill="#A5B4FC">{xml_text(date_label)}</text>
 <text x="3610" y="2090" font-size="21" text-anchor="end" letter-spacing="4" fill="{c}">INDEPENDENT TECHNOLOGY EDITORIAL</text>
</g>
<g>{scene}</g>
<g opacity=".24"><path class="scan" d="M2350 620H3540" stroke="{c}" stroke-width="3" stroke-dasharray="18 22"/></g>
<path d="M220 1940H3610" stroke="#94A3B8" stroke-opacity=".24" stroke-width="2"/>
</svg>'''


def build_json(data: dict[str, str], timestamp: datetime) -> str:
    return json.dumps({**data, "date": timestamp.isoformat(timespec="seconds"), "author": AUTHOR, "generator": "Junaeid Tech Journal 3.0"}, ensure_ascii=False, indent=2) + "\n"


def build_readme_section(timestamp: datetime) -> str:
    # The README is a gallery surface only. No duplicated title, explanation, or takeaway.
    cache = timestamp.strftime("%Y%m%d%H%M%S")
    return f'<div align="center">\n\n![Junaeid Tech Journal — Animated 4K Insight](assets/ai-tech-insight.svg?v={cache})\n\n</div>'


def update_readme(existing: str, section: str) -> str:
    if existing.count(START_MARKER) != 1 or existing.count(END_MARKER) != 1:
        raise ValidationError("README must contain exactly one INSIGHT:START and INSIGHT:END marker")
    start, end = existing.index(START_MARKER), existing.index(END_MARKER)
    if start >= end:
        raise ValidationError("README insight markers are incorrectly ordered")
    return existing[:start + len(START_MARKER)] + "\n\n" + section + "\n\n" + existing[end:]


def validate_svg(content: str, data: dict[str, str]) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValidationError(f"Invalid SVG/XML: {exc}") from exc
    if not root.tag.endswith("svg"):
        raise ValidationError("Artwork root is not SVG")
    if root.attrib.get("width") != "3840" or root.attrib.get("height") != "2160" or root.attrib.get("viewBox") != "0 0 3840 2160":
        raise ValidationError("Artwork must use a 3840 × 2160 canvas")
    # SVG line wrapping creates separate text nodes, so compare every editorial
    # token rather than requiring the original paragraph to remain one line.
    visible = " ".join((node.text or "") for node in root.iter() if node.tag.endswith("text"))
    normalized_visible = " ".join(visible.split()).casefold()
    for field in ("title", "explanation", "takeaway"):
        for token in re.findall(r"[\w'-]+", data[field], flags=re.UNICODE):
            if token.casefold() not in normalized_visible:
                raise ValidationError(f"Artwork omitted {field} word: {token}")
    lowered = content.casefold()
    stripped = lowered.replace('xmlns="http://www.w3.org/2000/svg"', "")
    if any(x in stripped for x in ("<script", "javascript:", "http://", "https://", "<!entity")):
        raise ValidationError("Artwork contains an unsafe external reference or script")
    if "prefers-reduced-motion" not in content:
        raise ValidationError("Artwork must include reduced-motion support")


def validate_json(content: str) -> None:
    try:
        validate_insight(json.loads(content))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid insight JSON: {exc}") from exc


def publish_files(files: dict[Path, str]) -> None:
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for target, content in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
            temp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged[target] = temp
        for target, temp in staged.items():
            os.replace(temp, target)
            replaced.append(target)
    except Exception:
        log.exception("Publication failed; restoring previous files")
        for target in reversed(replaced):
            original = originals.get(target)
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".rollback", dir=str(target.parent))
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, target)
            except Exception:
                log.critical("Rollback failed for %s", target, exc_info=True)
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)


def main() -> int:
    log.info("=" * 64)
    log.info("JUNAEID TECH JOURNAL | ANIMATED 4K EDITION")
    log.info("Created by %s", AUTHOR)
    log.info("=" * 64)
    try:
        if not README_FILE.is_file():
            raise ValidationError("README.md was not found")
        readme = README_FILE.read_text(encoding="utf-8")
        if readme.count(START_MARKER) != 1 or readme.count(END_MARKER) != 1 or readme.index(START_MARKER) >= readme.index(END_MARKER):
            raise ValidationError("README insight markers are missing, duplicated, or incorrectly ordered")
        timestamp = now_dhaka()
        insight = generate_insight()
        log.info("Generated: %s", insight["title"])
        svg = build_svg(insight, timestamp)
        json_content = build_json(insight, timestamp)
        readme_content = update_readme(readme, build_readme_section(timestamp))
        validate_svg(svg, insight)
        validate_json(json_content)
        if not readme_content.strip():
            raise ValidationError("README output is empty")
        publish_files({JSON_FILE: json_content, SVG_FILE: svg, README_FILE: readme_content})
        log.info("PUBLICATION SUCCESSFUL")
        log.info("Artwork: 3840 × 2160 animated SVG | Scene: %s", scene_kind(insight))
        log.info("README: artwork embed only; all editorial copy is inside the artwork")
        return 0
    except Exception as exc:
        log.error("TECH INSIGHT GENERATION FAILED: %s", exc)
        log.error("Existing publication was preserved")
        if os.getenv("DEBUG", "").lower() in ("1", "true", "yes"):
            log.exception("Debug traceback")
        return 1


if __name__ == "__main__":
    sys.exit(main())
