
import html
import json
import math
import os
import random
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# BRAND CONFIGURATION
# ============================================================

OWNER = "Muhammad Junaeid"
MODEL = "gemini-3.5-flash-lite"
TIMEZONE = ZoneInfo("Asia/Dhaka")

ROOT = Path(__file__).resolve().parent.parent

README_PATH = ROOT / "README.md"
DATA_PATH = ROOT / "data" / "insight.json"
SVG_PATH = ROOT / "assets" / "ai-tech-insight.svg"

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

API_TIMEOUT = 45
MAX_ATTEMPTS = 3

CATEGORIES = {
    "Artificial Intelligence",
    "Web Development",
    "Cybersecurity",
    "Software Engineering",
    "Emerging Technology",
    "Developer Productivity",
    "Cloud Computing",
    "Data and Privacy",
}

REQUIRED_FIELDS = {
    "title",
    "category",
    "explanation",
    "takeaway",
}

# Each category has its own visual identity.
THEMES = {
    "Artificial Intelligence": {
        "primary": "#22D3EE",
        "secondary": "#818CF8",
        "glow": "#38BDF8",
        "background": "#0B1020",
        "motif": "neural",
    },
    "Cybersecurity": {
        "primary": "#34D399",
        "secondary": "#22D3EE",
        "glow": "#10B981",
        "background": "#081713",
        "motif": "security",
    },
    "Web Development": {
        "primary": "#60A5FA",
        "secondary": "#A78BFA",
        "glow": "#3B82F6",
        "background": "#0C1224",
        "motif": "web",
    },
    "Software Engineering": {
        "primary": "#A78BFA",
        "secondary": "#22D3EE",
        "glow": "#8B5CF6",
        "background": "#100D20",
        "motif": "architecture",
    },
    "Emerging Technology": {
        "primary": "#F472B6",
        "secondary": "#818CF8",
        "glow": "#EC4899",
        "background": "#180D20",
        "motif": "emerging",
    },
    "Developer Productivity": {
        "primary": "#FBBF24",
        "secondary": "#22D3EE",
        "glow": "#F59E0B",
        "background": "#171307",
        "motif": "productivity",
    },
    "Cloud Computing": {
        "primary": "#38BDF8",
        "secondary": "#818CF8",
        "glow": "#0EA5E9",
        "background": "#081625",
        "motif": "cloud",
    },
    "Data and Privacy": {
        "primary": "#2DD4BF",
        "secondary": "#60A5FA",
        "glow": "#14B8A6",
        "background": "#071A1A",
        "motif": "data",
    },
}


# ============================================================
# FILE UTILITIES
# ============================================================

def log(message):
    print(message, flush=True)


def today():
    return datetime.now(TIMEZONE).date().isoformat()


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            delete=False,
        ) as file:
            file.write(content)
            temporary = Path(file.name)

        os.replace(temporary, path)

    except Exception:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)
        raise


def read_readme():
    if not README_PATH.is_file():
        raise RuntimeError("README.md does not exist.")

    content = README_PATH.read_text(encoding="utf-8")

    if content.count(START_MARKER) != 1:
        raise RuntimeError("README requires exactly one START marker.")

    if content.count(END_MARKER) != 1:
        raise RuntimeError("README requires exactly one END marker.")

    if content.index(START_MARKER) >= content.index(END_MARKER):
        raise RuntimeError("README markers are incorrectly ordered.")

    return content


def read_history():
    if not DATA_PATH.exists():
        return {}

    try:
        result = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Existing insight.json is invalid.") from None

    if not isinstance(result, dict):
        raise RuntimeError("Insight history must be a JSON object.")

    return result


# ============================================================
# TEXT VALIDATION
# ============================================================

def clean_text(value, field, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")

    value = " ".join(value.split()).strip()

    if not value or len(value) > maximum:
        raise ValueError(f"Invalid {field} length.")

    if "<" in value or ">" in value:
        raise ValueError(f"HTML is not allowed in {field}.")

    if re.search(r"https?://|www\.", value, re.I):
        raise ValueError(f"URLs are not allowed in {field}.")

    if any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid control characters in {field}.")

    return value


def validate_insight(data):
    if not isinstance(data, dict):
        raise ValueError("Insight must be a JSON object.")

    if set(data) != REQUIRED_FIELDS:
        raise ValueError("Unexpected insight JSON fields.")

    result = {
        "title": clean_text(data["title"], "title", 120),
        "category": clean_text(data["category"], "category", 40),
        "explanation": clean_text(
            data["explanation"], "explanation", 1200
        ),
        "takeaway": clean_text(
            data["takeaway"], "takeaway", 350
        ),
    }

    if result["category"] not in CATEGORIES:
        raise ValueError("Unapproved technology category.")

    return result


# ============================================================
# GEMINI API
# ============================================================

def build_prompt(previous):
    return f"""
You are a senior technology educator and software engineer.

Create one original and educational technology insight
for a professional developer's GitHub profile.

Previous title: {previous.get("title", "None")}
Previous category: {previous.get("category", "None")}

Choose exactly one category:
{", ".join(sorted(CATEGORIES))}

CONTENT REQUIREMENTS:

1. Explain a meaningful technical concept.
2. Use technically accurate information.
3. Explain the concept in enough detail to teach it.
4. Explanation should be approximately 70-110 words.
5. Takeaway should be approximately 15-25 words.
6. Title should be concise and interesting.
7. Avoid repeating the previous topic.
8. Avoid fabricated statistics and citations.
9. Use clear, professional English.
10. Do not use HTML, Markdown, URLs or emojis.

Return ONLY valid JSON:

{{
  "title": "Insight title",
  "category": "Approved category",
  "explanation": "Complete educational explanation",
  "takeaway": "Practical lesson"
}}
""".strip()


def generate_insight(api_key, previous):
    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{MODEL}:generateContent"
    )

    payload = {
        "contents": [{
            "parts": [{
                "text": build_prompt(previous)
            }]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.85,
            "maxOutputTokens": 1024,
        },
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"Gemini request {attempt}/{MAX_ATTEMPTS}")

        try:
            with urllib.request.urlopen(
                request,
                timeout=API_TIMEOUT,
            ) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )

            candidates = result.get("candidates", [])

            if not candidates:
                raise RuntimeError("Gemini returned no candidates.")

            parts = candidates[0].get(
                "content", {}
            ).get("parts", [])

            output = "".join(
                part["text"]
                for part in parts
                if isinstance(part, dict)
                and isinstance(part.get("text"), str)
            ).strip()

            if not output:
                raise RuntimeError("Gemini returned empty content.")

            return validate_insight(json.loads(output))

        except urllib.error.HTTPError as error:
            if error.code not in {408, 429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Gemini HTTP error: {error.code}"
                ) from None

            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Gemini unavailable: HTTP {error.code}"
                ) from None

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ):
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Gemini network request failed."
                ) from None

        except json.JSONDecodeError:
            raise RuntimeError("Gemini returned invalid JSON.") from None

        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2 ** attempt, 8) + random.random())

    raise RuntimeError("Insight generation failed.")


# ============================================================
# DYNAMIC SVG TEXT LAYOUT
# ============================================================

def wrap_text(text, max_chars):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        # Break extremely long words without losing characters.
        while len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""

            lines.append(word[:max_chars])
            word = word[max_chars:]

        candidate = f"{current} {word}".strip()

        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    return lines


def render_text(lines, x, y, size, color, weight, spacing):
    elements = []

    for index, line in enumerate(lines):
        elements.append(
            f'<text x="{x}" y="{y + index * spacing}" '
            f'font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{html.escape(line)}</text>'
        )

    return "\n".join(elements)


# ============================================================
# CATEGORY-SPECIFIC ARTWORK
# ============================================================

def create_artwork(theme, seed):
    rng = random.Random(seed)

    primary = theme["primary"]
    secondary = theme["secondary"]
    motif = theme["motif"]

    parts = []

    if motif == "neural":
        nodes = [
            (800, 55), (930, 110), (1060, 48),
            (850, 165), (1000, 195), (1130, 130),
            (950, 25), (1090, 220)
        ]

        for i, (x1, y1) in enumerate(nodes):
            for x2, y2 in nodes[i+1:]:
                if abs(x1-x2) + abs(y1-y2) < 280:
                    parts.append(
                        f'<path d="M{x1} {y1} L{x2} {y2}" '
                        f'stroke="{primary}" opacity=".24" '
                        f'stroke-width="1.5"/>'
                    )

        for i, (x, y) in enumerate(nodes):
            radius = rng.randint(4, 8)
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="{radius+8}" '
                f'fill="{primary}" opacity=".08"/>'
                f'<circle cx="{x}" cy="{y}" r="{radius}" '
                f'fill="{secondary if i % 2 else primary}"/>'
            )

    elif motif == "security":
        parts.append(
            f'<path d="M970 20 L1100 70 L1085 160 '
            f'Q1040 215 970 245 Q900 215 855 160 '
            f'L840 70 Z" fill="none" stroke="{primary}" '
            f'stroke-width="3" opacity=".55"/>'
        )
        parts.append(
            f'<rect x="945" y="100" width="50" height="45" '
            f'rx="8" fill="none" stroke="{secondary}" stroke-width="3"/>'
            f'<path d="M955 100 V85 A15 15 0 0 1 985 85 V100" '
            f'fill="none" stroke="{secondary}" stroke-width="3"/>'
        )

    elif motif == "web":
        for i in range(3):
            x = 835 + i * 95
            parts.append(
                f'<rect x="{x}" y="{60+i*23}" width="80" height="115" '
                f'rx="9" fill="{primary}" fill-opacity=".06" '
                f'stroke="{secondary}" stroke-opacity=".65"/>'
            )
            for j in range(3):
                parts.append(
                    f'<line x1="{x+13}" y1="{90+i*23+j*17}" '
                    f'x2="{x+65}" y2="{90+i*23+j*17}" '
                    f'stroke="{primary}" opacity=".5"/>'
                )

    elif motif == "architecture":
        for i in range(4):
            x = 830 + i * 75
            y = 45 + (i % 2) * 70
            parts.append(
                f'<rect x="{x}" y="{y}" width="55" height="55" '
                f'rx="9" fill="{primary}" fill-opacity=".08" '
                f'stroke="{secondary}" stroke-width="2"/>'
            )
            if i < 3:
                parts.append(
                    f'<path d="M{x+55} {y+27} '
                    f'L{x+75} {y+27}" stroke="{primary}" '
                    f'stroke-width="2"/>'
                )

    elif motif == "cloud":
        parts.append(
            f'<path d="M850 160 A35 35 0 0 1 875 95 '
            f'A65 65 0 0 1 1000 85 A50 50 0 0 1 1060 160 '
            f'Z" fill="{primary}" fill-opacity=".10" '
            f'stroke="{secondary}" stroke-width="3"/>'
        )
        for i in range(3):
            parts.append(
                f'<path d="M{900+i*55} 175 V215" '
                f'stroke="{primary}" stroke-width="2" opacity=".6"/>'
            )

    elif motif == "data":
        for i in range(5):
            x = 850 + i * 52
            h = rng.randint(40, 130)
            parts.append(
                f'<rect x="{x}" y="{190-h}" width="22" '
                f'height="{h}" rx="5" fill="{primary}" '
                f'fill-opacity=".65"/>'
            )

    elif motif == "productivity":
        for i in range(3):
            parts.append(
                f'<circle cx="{930+i*70}" cy="{115}" '
                f'r="{25+i*12}" fill="none" '
                f'stroke="{primary if i % 2 == 0 else secondary}" '
                f'stroke-width="2" opacity=".6"/>'
            )

    else:
        for i in range(8):
            x = rng.randint(830, 1120)
            y = rng.randint(25, 220)
            r = rng.randint(5, 15)
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="{r}" '
                f'fill="{primary}" fill-opacity=".45"/>'
            )

    # Add topic-independent motion accents.
    parts.append(
        f'<circle cx="1010" cy="110" r="125" '
        f'fill="none" stroke="{primary}" '
        f'stroke-width="1" opacity=".18" '
        f'stroke-dasharray="5 12" '
        f'class="orbit"/>'
    )

    parts.append(
        f'<circle cx="1010" cy="110" r="145" '
        f'fill="none" stroke="{secondary}" '
        f'stroke-width="1" opacity=".12" '
        f'stroke-dasharray="2 15" class="orbit reverse"/>'
    )

    return "\n".join(parts)


# ============================================================
# DYNAMIC SVG GENERATOR
# ============================================================

def create_svg(insight, date):
    theme = THEMES[insight["category"]]

    seed = sum(
        ord(char)
        for char in (date + insight["title"])
    )

    rng = random.Random(seed)

    margin = 76
    width = 1200

    # Content is never restricted to a fixed height.
    title_lines = wrap_text(insight["title"], 42)
    explanation_lines = wrap_text(insight["explanation"], 108)
    takeaway_lines = wrap_text(insight["takeaway"], 108)

    # Variable title and body typography.
    title_size = 40 if len(title_lines) <= 2 else 34
    title_spacing = title_size * 1.35

    body_size = 18
    body_spacing = 31

    takeaway_size = 17
    takeaway_spacing = 28

    # Dynamic positioning.
    header_y = 70
    category_y = 128
    title_y = 218

    title_height = len(title_lines) * title_spacing

    divider_y = title_y + title_height + 28
    explanation_y = divider_y + 62

    explanation_height = (
        len(explanation_lines) * body_spacing
    )

    takeaway_top = explanation_y + explanation_height + 38

    takeaway_height = (
        80 + len(takeaway_lines) * takeaway_spacing
    )

    footer_divider = takeaway_top + takeaway_height + 48
    footer_y = footer_divider + 42

    # Add generous bottom breathing room.
    height = math.ceil(footer_y + 65)

    primary = theme["primary"]
    secondary = theme["secondary"]
    background = theme["background"]

    artwork = create_artwork(theme, seed)

    title_svg = render_text(
        title_lines, margin, title_y,
        title_size, "#F8FAFC", 700, title_spacing
    )

    explanation_svg = render_text(
        explanation_lines, margin, explanation_y,
        body_size, "#CBD5E1", 400, body_spacing
    )

    takeaway_svg = render_text(
        takeaway_lines, margin + 24,
        takeaway_top + 62,
        takeaway_size, "#F8FAFC", 600, takeaway_spacing
    )

    # Deterministic daily background particles.
    particles = []

    for _ in range(22):
        x = rng.randint(40, 1160)
        y = rng.randint(20, 230)
        radius = rng.randint(1, 3)
        delay = rng.randint(0, 8)

        particles.append(
            f'<circle cx="{x}" cy="{y}" r="{radius}" '
            f'fill="{primary}" opacity=".4" '
            f'class="particle" style="animation-delay:-{delay}s"/>'
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg"
width="{width}" height="{height}"
viewBox="0 0 {width} {height}"
role="img" aria-labelledby="card-title card-description">

<title id="card-title">{html.escape(insight["title"])}</title>
<desc id="card-description">
{html.escape(insight["explanation"])}
</desc>

<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{background}"/>
    <stop offset="100%" stop-color="#17243A"/>
  </linearGradient>

  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="{primary}"/>
    <stop offset="100%" stop-color="{secondary}"/>
  </linearGradient>

  <radialGradient id="glow">
    <stop offset="0%" stop-color="{theme["glow"]}" stop-opacity=".18"/>
    <stop offset="100%" stop-color="{theme["glow"]}" stop-opacity="0"/>
  </radialGradient>

  <pattern id="grid" width="34" height="34"
    patternUnits="userSpaceOnUse">
    <circle cx="1" cy="1" r="1" fill="#94A3B8" opacity=".17"/>
  </pattern>
</defs>

<style>
  .particle {{
    animation: float 7s ease-in-out infinite alternate;
  }}

  .orbit {{
    transform-box: fill-box;
    transform-origin: center;
    animation: rotate 35s linear infinite;
  }}

  .orbit.reverse {{
    animation-direction: reverse;
    animation-duration: 48s;
  }}

  .glow-pulse {{
    animation: pulse 5s ease-in-out infinite alternate;
  }}

  @keyframes float {{
    from {{ transform: translateY(-5px); opacity: .2; }}
    to {{ transform: translateY(12px); opacity: .8; }}
  }}

  @keyframes rotate {{
    from {{ transform: rotate(0deg); }}
    to {{ transform: rotate(360deg); }}
  }}

  @keyframes pulse {{
    from {{ opacity: .12; }}
    to {{ opacity: .28; }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    .particle, .orbit, .glow-pulse {{
      animation: none;
    }}
  }}
</style>

<!-- Background -->
<rect width="1200" height="{height}" rx="22"
  fill="url(#background)"/>

<rect width="1200" height="{height}"
  fill="url(#grid)"/>

<rect x="0" y="0" width="7" height="{height}"
  fill="url(#accent)"/>

<circle cx="1010" cy="110" r="280"
  fill="url(#glow)" class="glow-pulse"/>

<!-- Animated particles -->
<g>{''.join(particles)}</g>

<!-- Subject-specific artwork -->
<g>{artwork}</g>

<!-- Header -->
<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="{margin}" y="{header_y}"
    fill="#F8FAFC" font-size="21"
    font-weight="700" letter-spacing="1.5">
    JUNAEID
    <tspan fill="{primary}"> / </tspan>
    TECH JOURNAL
  </text>

  <text x="1124" y="{header_y}"
    text-anchor="end" fill="#94A3B8"
    font-size="14">
    {html.escape(date)}
  </text>

  <!-- Category -->
  <rect x="{margin}" y="105" width="7" height="24"
    rx="3" fill="url(#accent)"/>

  <text x="{margin+20}" y="{category_y}"
    fill="{primary}" font-size="14"
    font-weight="700" letter-spacing="1.3">
    {html.escape(insight["category"].upper())}
  </text>

  <!-- Complete title -->
  {title_svg}

  <line x1="{margin}" y1="{divider_y}"
    x2="1124" y2="{divider_y}"
    stroke="#64748B" opacity=".5"/>

  <!-- Complete explanation -->
  {explanation_svg}

  <!-- Takeaway -->
  <rect x="{margin}" y="{takeaway_top}"
    width="1048" height="{takeaway_height}"
    rx="14" fill="#FFFFFF" fill-opacity=".055"/>

  <rect x="{margin}" y="{takeaway_top}"
    width="4" height="{takeaway_height}"
    rx="2" fill="url(#accent)"/>

  <text x="{margin+24}" y="{takeaway_top+30}"
    font-size="12" font-weight="700"
    fill="{primary}" letter-spacing="1.5">
    KEY TAKEAWAY
  </text>

  {takeaway_svg}

  <!-- Footer -->
  <line x1="{margin}" y1="{footer_divider}"
    x2="1124" y2="{footer_divider}"
    stroke="#64748B" opacity=".5"/>

  <text x="{margin}" y="{footer_y}"
    font-size="14" font-weight="700"
    fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="1124" y="{footer_y}"
    font-size="13" text-anchor="end"
    fill="{primary}">
    AI-ASSISTED TECHNOLOGY JOURNAL
  </text>

</g>
</svg>
'''


# ============================================================
# README INTEGRATION
# ============================================================

def update_readme(content):
    start = content.index(START_MARKER) + len(START_MARKER)
    end = content.index(END_MARKER)

    section = (
        "\n\n"
        "![AI Tech Insight by Muhammad Junaeid]"
        "(./assets/ai-tech-insight.svg)"
        "\n\n"
    )

    result = content[:start] + section + content[end:]

    result = result.replace("\r\n", "\n").replace("\r", "\n")

    result = "\n".join(
        line.rstrip(" \t")
        for line in result.splitlines()
    ).rstrip() + "\n"

    return result


# ============================================================
# MAIN
# ============================================================

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    date = today()

    log("Checking repository files...")

    readme = read_readme()
    previous = read_history()

    # Reuse valid insight from today.
    if previous.get("date") == date:
        log("Today's insight already exists. Rebuilding visual assets.")

        insight = validate_insight({
            key: previous[key]
            for key in REQUIRED_FIELDS
        })

    else:
        log("Generating a new technology insight...")
        insight = generate_insight(api_key, previous)

    log("Creating topic-specific animated SVG...")

    svg_content = create_svg(insight, date)

    # Parse SVG before writing.
    try:
        root = ET.fromstring(svg_content)
    except ET.ParseError as error:
        raise RuntimeError(f"SVG XML validation failed: {error}") from None

    if not root.tag.endswith("svg"):
        raise RuntimeError("Invalid SVG root element.")

    if root.attrib.get("width") != "1200":
        raise RuntimeError("Invalid SVG width.")

    if int(root.attrib.get("height", "0")) <= 630:
        raise RuntimeError("Dynamic SVG height was not generated correctly.")

    log(f"SVG canvas: 1200 x {root.attrib['height']}")

    updated_readme = update_readme(readme)

    history = {
        **insight,
        "date": date,
        "author": OWNER,
        "provider": "Google Gemini",
        "model": MODEL,
    }

    json_content = json.dumps(
        history,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    # Prepare all outputs before replacing files.
    json.loads(json_content)

    log("Writing JSON...")
    atomic_write(DATA_PATH, json_content)

    log("Writing animated SVG...")
    atomic_write(SVG_PATH, svg_content)

    log("Updating README...")
    atomic_write(README_PATH, updated_readme)

    # Final verification.
    for path in (README_PATH, DATA_PATH, SVG_PATH):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Output verification failed: {path.name}")

    log("========================================")
    log("AI TECH INSIGHT GENERATED SUCCESSFULLY")
    log(f"Date: {date}")
    log(f"Title: {insight['title']}")
    log(f"Category: {insight['category']}")
    log(f"SVG height: {root.attrib['height']}px")
    log(f"Author: {OWNER}")
    log("========================================")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"Workflow failed safely: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
