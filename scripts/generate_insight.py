import hashlib
import html
import json
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
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

README_PATH = ROOT / "README.md"
DATA_PATH = ROOT / "data" / "insight.json"
SVG_PATH = ROOT / "assets" / "ai-tech-insight.svg"

OWNER = "MUHAMMAD JUNAEID"
MODEL = "gemini-3.5-flash-lite"
TIMEZONE = ZoneInfo("Asia/Dhaka")

API_TIMEOUT = 50
MAX_ATTEMPTS = 3
RECENT_TITLE_LIMIT = 20

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

CATEGORIES = [
    "Artificial Intelligence",
    "Web Development",
    "Cybersecurity",
    "Software Engineering",
    "Emerging Technology",
    "Developer Productivity",
    "Cloud Computing",
    "Data and Privacy",
]

# Each category has a distinct visual language.
THEMES = {
    "Artificial Intelligence": {
        "motif": "neural",
        "colors": [
            ("#22D3EE", "#818CF8", "#0B1020"),
            ("#A78BFA", "#38BDF8", "#100D25"),
            ("#34D399", "#60A5FA", "#091A20"),
        ],
    },
    "Web Development": {
        "motif": "web",
        "colors": [
            ("#60A5FA", "#A78BFA", "#0C1224"),
            ("#2DD4BF", "#818CF8", "#071B24"),
            ("#F472B6", "#60A5FA", "#180F25"),
        ],
    },
    "Cybersecurity": {
        "motif": "security",
        "colors": [
            ("#34D399", "#22D3EE", "#081713"),
            ("#2DD4BF", "#60A5FA", "#071A1A"),
            ("#FBBF24", "#34D399", "#17190C"),
        ],
    },
    "Software Engineering": {
        "motif": "architecture",
        "colors": [
            ("#A78BFA", "#22D3EE", "#100D20"),
            ("#818CF8", "#F472B6", "#160D25"),
            ("#60A5FA", "#34D399", "#091625"),
        ],
    },
    "Emerging Technology": {
        "motif": "emerging",
        "colors": [
            ("#F472B6", "#818CF8", "#180D20"),
            ("#22D3EE", "#FBBF24", "#101729"),
            ("#A78BFA", "#34D399", "#120F25"),
        ],
    },
    "Developer Productivity": {
        "motif": "productivity",
        "colors": [
            ("#FBBF24", "#22D3EE", "#171307"),
            ("#34D399", "#A78BFA", "#0C1B17"),
            ("#60A5FA", "#F472B6", "#151329"),
        ],
    },
    "Cloud Computing": {
        "motif": "cloud",
        "colors": [
            ("#38BDF8", "#818CF8", "#081625"),
            ("#22D3EE", "#34D399", "#071C23"),
            ("#A78BFA", "#60A5FA", "#10152A"),
        ],
    },
    "Data and Privacy": {
        "motif": "data",
        "colors": [
            ("#2DD4BF", "#60A5FA", "#071A1A"),
            ("#34D399", "#A78BFA", "#0B191C"),
            ("#FBBF24", "#22D3EE", "#17180D"),
        ],
    },
}


# ============================================================
# GENERAL UTILITIES
# ============================================================

def log(message):
    print(message, flush=True)


def current_time():
    return datetime.now(TIMEZONE)


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
        raise RuntimeError("README START marker must appear once.")

    if content.count(END_MARKER) != 1:
        raise RuntimeError("README END marker must appear once.")

    if content.index(START_MARKER) > content.index(END_MARKER):
        raise RuntimeError("README markers are incorrectly ordered.")

    return content


def read_history():
    if not DATA_PATH.exists():
        return {}

    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Existing insight.json is invalid.") from None

    if not isinstance(data, dict):
        raise RuntimeError("Insight history must be a JSON object.")

    return data


# ============================================================
# CONTENT VALIDATION
# ============================================================

def clean_text(value, field, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")

    value = " ".join(value.split()).strip()

    if not value or len(value) > maximum:
        raise ValueError(f"Invalid {field} length.")

    if "<" in value or ">" in value:
        raise ValueError(f"HTML is not permitted in {field}.")

    if re.search(r"https?://|www\.", value, re.I):
        raise ValueError(f"URLs are not permitted in {field}.")

    if any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid control characters in {field}.")

    return value


def validate_insight(data):
    required = {
        "title",
        "category",
        "explanation",
        "takeaway",
    }

    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("Gemini returned an unexpected JSON structure.")

    result = {
        "title": clean_text(data["title"], "title", 120),
        "category": clean_text(data["category"], "category", 40),
        "explanation": clean_text(
            data["explanation"], "explanation", 1500
        ),
        "takeaway": clean_text(
            data["takeaway"], "takeaway", 400
        ),
    }

    if result["category"] not in CATEGORIES:
        raise ValueError("Gemini returned an unsupported category.")

    if len(result["explanation"]) < 180:
        raise ValueError("Explanation is too short.")

    if len(result["takeaway"]) < 25:
        raise ValueError("Takeaway is too short.")

    return result


# ============================================================
# GEMINI API
# ============================================================

def build_prompt(previous):
    recent = previous.get("recent_titles", [])

    if not isinstance(recent, list):
        recent = []

    recent = [
        str(title)[:120]
        for title in recent[-RECENT_TITLE_LIMIT:]
    ]

    recent_text = "\n".join(
        f"- {title}" for title in recent
    ) or "- No previous insights"

    return f"""
You are an expert technology researcher, senior software engineer,
cybersecurity educator and technical writer.

Create one original, technically accurate insight for a
professional developer's public GitHub profile.

RECENTLY PUBLISHED TITLES:
{recent_text}

Choose exactly ONE category from:
{", ".join(CATEGORIES)}

CONTENT REQUIREMENTS:

1. Introduce an interesting technical concept, discovery,
   engineering practice or emerging technology.
2. Avoid the concepts and titles in the recent history.
3. Explain WHY the concept matters.
4. Explain HOW it works.
5. Give a practical real-world developer or technology use case.
6. Prefer technically meaningful subjects over generic AI news.
7. Never fabricate statistics, research papers, releases or facts.
8. Use clear professional English.
9. Make the explanation approximately 90-130 words.
10. Make the takeaway approximately 20-35 words.
11. The title should be concise and compelling.
12. Do not include Markdown, HTML, URLs or emojis.

Return ONLY valid JSON with these exact fields:

{{
  "title": "Concise technical title",
  "category": "One approved category",
  "explanation": "Complete technical explanation",
  "takeaway": "Practical lesson"
}}
""".strip()


def request_gemini(api_key, previous):
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
            "maxOutputTokens": 1400,
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
        log(f"Gemini generation attempt {attempt}/{MAX_ATTEMPTS}")

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
                part.get("text", "")
                for part in parts
                if isinstance(part, dict)
            ).strip()

            if not output:
                raise RuntimeError("Gemini returned an empty response.")

            return json.loads(output)

        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            log(f"Gemini HTTP {error.code}: {details[:500]}")

            if error.code not in {408, 429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Gemini API rejected the request: HTTP {error.code}"
                ) from None

            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Gemini API remained unavailable after retries."
                ) from None

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ):
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Gemini network request timed out."
                ) from None

        except json.JSONDecodeError:
            raise RuntimeError(
                "Gemini returned invalid JSON."
            ) from None

        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2 ** attempt, 8))

    raise RuntimeError("Gemini generation failed.")


def generate_unique_insight(api_key, previous):
    recent = previous.get("recent_titles", [])

    if not isinstance(recent, list):
        recent = []

    for attempt in range(1, 4):
        log(f"Checking originality: {attempt}/3")

        raw = request_gemini(api_key, previous)
        insight = validate_insight(raw)

        title_key = insight["title"].casefold().strip()

        recent_keys = {
            str(item).casefold().strip()
            for item in recent
        }

        if title_key in recent_keys:
            log("Duplicate title detected. Generating another.")
            continue

        return insight

    raise RuntimeError(
        "Could not generate a title distinct from recent publications."
    )


# ============================================================
# TEXT LAYOUT
# ============================================================

def wrap_text(text, max_chars):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        if len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""

            while len(word) > max_chars:
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


def render_lines(lines, x, y, size, color, weight, spacing):
    elements = []

    for index, line in enumerate(lines):
        elements.append(
            f'<text x="{x}" y="{y + index * spacing}" '
            f'font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{html.escape(line)}</text>'
        )

    return "\n".join(elements)


# ============================================================
# DYNAMIC TOPIC GRAPHICS
# ============================================================

def create_artwork(motif, primary, secondary, rng):
    elements = []

    if motif == "neural":
        nodes = [
            (870, 60), (970, 30), (1080, 75),
            (890, 155), (1000, 180), (1120, 155),
            (980, 110), (1070, 230),
        ]

        for i, (x1, y1) in enumerate(nodes):
            for x2, y2 in nodes[i + 1:]:
                if abs(x1 - x2) + abs(y1 - y2) < 260:
                    elements.append(
                        f'<line x1="{x1}" y1="{y1}" '
                        f'x2="{x2}" y2="{y2}" '
                        f'stroke="{primary}" opacity=".35"/>'
                    )

        for i, (x, y) in enumerate(nodes):
            color = primary if i % 2 == 0 else secondary
            elements.append(
                f'<circle cx="{x}" cy="{y}" r="8" fill="{color}"/>'
            )

    elif motif == "security":
        elements.append(
            f'<path d="M980 25 L1100 75 L1085 165 '
            f'Q1040 220 980 250 Q920 220 875 165 '
            f'L860 75 Z" fill="none" stroke="{primary}" '
            f'stroke-width="4"/>'
        )
        elements.append(
            f'<rect x="950" y="105" width="60" height="52" '
            f'rx="9" fill="none" stroke="{secondary}" stroke-width="4"/>'
        )
        elements.append(
            f'<path d="M965 105 V90 A15 15 0 0 1 995 90 V105" '
            f'fill="none" stroke="{secondary}" stroke-width="4"/>'
        )

    elif motif == "web":
        for i in range(3):
            x = 840 + i * 100
            y = 65 + (i % 2) * 25

            elements.append(
                f'<rect x="{x}" y="{y}" width="75" height="125" '
                f'rx="10" fill="{primary}" fill-opacity=".08" '
                f'stroke="{secondary}" stroke-width="2"/>'
            )
            elements.append(
                f'<line x1="{x+12}" y1="{y+25}" '
                f'x2="{x+62}" y2="{y+25}" '
                f'stroke="{primary}" stroke-width="3"/>'
            )

    elif motif == "architecture":
        for i in range(4):
            x = 840 + i * 75
            y = 65 + (i % 2) * 70

            elements.append(
                f'<rect x="{x}" y="{y}" width="55" height="55" '
                f'rx="9" fill="{primary}" fill-opacity=".12" '
                f'stroke="{secondary}" stroke-width="2"/>'
            )

    elif motif == "cloud":
        elements.append(
            f'<path d="M850 160 A40 40 0 0 1 900 100 '
            f'A70 70 0 0 1 1015 85 A55 55 0 0 1 1080 160 Z" '
            f'fill="{primary}" fill-opacity=".12" '
            f'stroke="{secondary}" stroke-width="3"/>'
        )

    elif motif == "data":
        for i in range(7):
            x = 850 + i * 38
            height = rng.randint(45, 155)

            elements.append(
                f'<rect x="{x}" y="{215-height}" width="20" '
                f'height="{height}" rx="5" '
                f'fill="{primary}" opacity=".75"/>'
            )

    elif motif == "productivity":
        for i in range(3):
            radius = 35 + i * 38
            color = primary if i % 2 == 0 else secondary

            elements.append(
                f'<circle cx="985" cy="130" r="{radius}" '
                f'fill="none" stroke="{color}" '
                f'stroke-width="2" opacity=".7"/>'
            )

    else:
        for _ in range(12):
            x = rng.randint(850, 1120)
            y = rng.randint(35, 240)
            radius = rng.randint(5, 18)

            elements.append(
                f'<circle cx="{x}" cy="{y}" r="{radius}" '
                f'fill="{primary}" opacity=".5"/>'
            )

    return "\n".join(elements)


# ============================================================
# COMPLETE DYNAMIC SVG
# ============================================================

def create_svg(insight, published_at, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    seed = int(digest[:12], 16)
    rng = random.Random(seed)

    theme = THEMES[insight["category"]]
    palette = theme["colors"][seed % len(theme["colors"])]

    primary, secondary, background = palette

    title_lines = wrap_text(insight["title"], 43)
    explanation_lines = wrap_text(insight["explanation"], 94)
    takeaway_lines = wrap_text(insight["takeaway"], 94)

    title_size = 40 if len(title_lines) <= 2 else 34
    title_spacing = int(title_size * 1.35)

    body_size = 18
    body_spacing = 32

    takeaway_size = 17
    takeaway_spacing = 29

    margin = 76
    title_y = 215

    title_end = title_y + (len(title_lines) - 1) * title_spacing
    divider_y = title_end + 48
    explanation_y = divider_y + 65

    explanation_end = (
        explanation_y
        + (len(explanation_lines) - 1) * body_spacing
    )

    panel_y = explanation_end + 65
    panel_height = 76 + len(takeaway_lines) * takeaway_spacing

    footer_divider = panel_y + panel_height + 48
    footer_y = footer_divider + 43

    height = footer_y + 75

    artwork = create_artwork(
        theme["motif"], primary, secondary, rng
    )

    particles = []

    for _ in range(28):
        x = rng.randint(30, 1170)
        y = rng.randint(20, 260)
        radius = rng.randint(1, 3)
        delay = rng.randint(0, 8)

        particles.append(
            f'<circle cx="{x}" cy="{y}" r="{radius}" '
            f'fill="{primary}" class="particle" '
            f'style="animation-delay:-{delay}s"/>'
        )

    title = html.escape(insight["title"])
    category = html.escape(insight["category"].upper())
    timestamp = html.escape(published_at)
    explanation = html.escape(insight["explanation"])

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
width="1200" height="{height}"
viewBox="0 0 1200 {height}"
role="img" aria-labelledby="svg-title svg-description">

<title id="svg-title">{title}</title>
<desc id="svg-description">{explanation}</desc>

<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{background}"/>
    <stop offset="100%" stop-color="#17243A"/>
  </linearGradient>

  <linearGradient id="accent">
    <stop offset="0%" stop-color="{primary}"/>
    <stop offset="100%" stop-color="{secondary}"/>
  </linearGradient>

  <radialGradient id="glow">
    <stop offset="0%" stop-color="{primary}" stop-opacity=".20"/>
    <stop offset="100%" stop-color="{primary}" stop-opacity="0"/>
  </radialGradient>

  <pattern id="grid" width="34" height="34"
    patternUnits="userSpaceOnUse">
    <circle cx="1" cy="1" r="1"
      fill="#94A3B8" opacity=".17"/>
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

  .pulse {{
    animation: pulse 4s ease-in-out infinite alternate;
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
    from {{ opacity: .1; }}
    to {{ opacity: .35; }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    .particle, .orbit, .pulse {{ animation: none; }}
  }}
</style>

<rect width="1200" height="{height}" rx="22"
  fill="url(#background)"/>

<rect width="1200" height="{height}" fill="url(#grid)"/>
<rect width="7" height="{height}" fill="url(#accent)"/>

<circle cx="1010" cy="120" r="270"
  fill="url(#glow)" class="pulse"/>

<g>
{''.join(particles)}
</g>

<g>
{artwork}
</g>

<g class="orbit">
  <circle cx="1010" cy="120" r="135"
    fill="none" stroke="{primary}"
    stroke-width="1" opacity=".25"
    stroke-dasharray="5 12"/>
</g>

<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="{margin}" y="70"
    fill="#F8FAFC" font-size="21"
    font-weight="700" letter-spacing="1.5">
    JUNAEID <tspan fill="{primary}">/</tspan> TECH JOURNAL
  </text>

  <text x="1124" y="70"
    text-anchor="end" font-size="14" fill="#94A3B8">
    {timestamp}
  </text>

  <rect x="{margin}" y="105" width="7" height="24"
    rx="3" fill="url(#accent)"/>

  <text x="{margin+20}" y="123"
    font-size="14" font-weight="700"
    letter-spacing="1.3" fill="{primary}">
    {category}
  </text>

  {render_lines(title_lines, margin, title_y, title_size,
                "#F8FAFC", 700, title_spacing)}

  <line x1="{margin}" y1="{divider_y}"
    x2="1124" y2="{divider_y}"
    stroke="#64748B" opacity=".5"/>

  {render_lines(explanation_lines, margin, explanation_y,
                body_size, "#CBD5E1", 400, body_spacing)}

  <rect x="{margin}" y="{panel_y}" width="1048"
    height="{panel_height}" rx="14"
    fill="#FFFFFF" fill-opacity=".055"/>

  <rect x="{margin}" y="{panel_y}" width="4"
    height="{panel_height}" rx="2"
    fill="url(#accent)"/>

  <text x="{margin+24}" y="{panel_y+31}"
    font-size="12" font-weight="700"
    fill="{primary}" letter-spacing="1.5">
    KEY TAKEAWAY
  </text>

  {render_lines(takeaway_lines, margin+24, panel_y+63,
                takeaway_size, "#F8FAFC", 600, takeaway_spacing)}

  <line x1="{margin}" y1="{footer_divider}"
    x2="1124" y2="{footer_divider}"
    stroke="#64748B" opacity=".5"/>

  <text x="{margin}" y="{footer_y}"
    font-size="14" font-weight="700" fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="1124" y="{footer_y}"
    font-size="13" text-anchor="end" fill="{primary}">
    AI-ASSISTED TECHNOLOGY JOURNAL
  </text>

</g>
</svg>
'''

    # Validate XML and confirm every content field is present.
    root = ET.fromstring(svg)

    if root.attrib.get("width") != "1200":
        raise RuntimeError("Incorrect SVG width.")

    if int(root.attrib["height"]) != height:
        raise RuntimeError("Incorrect SVG height.")

    if insight["explanation"] not in "".join(root.itertext()):
        raise RuntimeError("SVG explanation was not fully rendered.")

    if insight["takeaway"] not in "".join(root.itertext()):
        raise RuntimeError("SVG takeaway was not fully rendered.")

    return svg


# ============================================================
# README UPDATE AND CACHE BUSTING
# ============================================================

def update_readme(content, token):
    start = content.index(START_MARKER) + len(START_MARKER)
    end = content.index(END_MARKER)

    section = (
        "\n\n"
        "![AI Tech Insight by Muhammad Junaeid]"
        f"(./assets/ai-tech-insight.svg?v={token})"
        "\n\n"
    )

    updated = content[:start] + section + content[end:]

    # Remove trailing whitespace from every line.
    updated = updated.replace("\r\n", "\n").replace("\r", "\n")
    updated = "\n".join(
        line.rstrip(" \t") for line in updated.splitlines()
    )

    return updated.rstrip() + "\n"


# ============================================================
# MAIN PUBLICATION PROCESS
# ============================================================

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    timestamp = current_time()
    published_at = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    token = timestamp.strftime("%Y%m%d%H%M%S")

    log("========================================")
    log("AI TECH INSIGHT GENERATOR")
    log("========================================")

    readme = read_readme()
    previous = read_history()

    recent_titles = previous.get("recent_titles", [])

    if not isinstance(recent_titles, list):
        recent_titles = []

    log("Generating a new insight...")
    insight = generate_unique_insight(api_key, previous)

    log("Generating new topic-specific SVG...")
    svg = create_svg(insight, published_at, token)

    log("Updating README image reference...")
    updated_readme = update_readme(readme, token)

    new_titles = (
        recent_titles + [insight["title"]]
    )[-RECENT_TITLE_LIMIT:]

    history = {
        **insight,
        "published_at": published_at,
        "timezone": "Asia/Dhaka",
        "cache_token": token,
        "author": OWNER,
        "provider": "Google Gemini",
        "model": MODEL,
        "recent_titles": new_titles,
    }

    json_content = json.dumps(
        history,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    # Validate all outputs before modifying the repository.
    json.loads(json_content)
    ET.fromstring(svg)

    if START_MARKER not in updated_readme:
        raise RuntimeError("README marker validation failed.")

    if f"?v={token}" not in updated_readme:
        raise RuntimeError("README cache token validation failed.")

    log("Publishing generated files...")

    atomic_write(DATA_PATH, json_content)
    atomic_write(SVG_PATH, svg)
    atomic_write(README_PATH, updated_readme)

    for path in (DATA_PATH, SVG_PATH, README_PATH):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Output verification failed: {path.name}")

    log("----------------------------------------")
    log("PUBLICATION SUCCESSFUL")
    log(f"Title: {insight['title']}")
    log(f"Category: {insight['category']}")
    log(f"Bangladesh time: {published_at}")
    log(f"SVG height: {ET.fromstring(svg).attrib['height']} px")
    log(f"Cache token: {token}")
    log(f"Author: {OWNER}")
    log("----------------------------------------")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"WORKFLOW FAILED SAFELY: {error}",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
