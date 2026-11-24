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
    """Draw a compact subject-specific illustration in a reserved area."""

    elements = []

    if motif == "security":
        # Shield with a centered lock. No timestamp or text in this region.
        elements.extend([
            f'<path d="M990 65 L1080 102 L1068 174 '
            f'Q1040 216 990 238 Q940 216 912 174 '
            f'L900 102 Z" fill="{primary}" fill-opacity=".055" '
            f'stroke="{primary}" stroke-width="3"/>',

            f'<rect x="967" y="133" width="46" height="43" '
            f'rx="8" fill="none" stroke="{secondary}" '
            f'stroke-width="3.5"/>',

            f'<path d="M978 133 V120 A12 12 0 0 1 1002 120 V133" '
            f'fill="none" stroke="{secondary}" stroke-width="3.5"/>',

            f'<circle cx="990" cy="151" r="3" fill="{secondary}"/>',
        ])

    elif motif == "neural":
        nodes = [
            (920, 90), (990, 65), (1060, 95),
            (930, 165), (1000, 145), (1070, 175),
            (990, 220),
        ]

        for i, (x1, y1) in enumerate(nodes):
            for x2, y2 in nodes[i + 1:]:
                if abs(x1-x2) + abs(y1-y2) < 190:
                    elements.append(
                        f'<line x1="{x1}" y1="{y1}" '
                        f'x2="{x2}" y2="{y2}" '
                        f'stroke="{primary}" opacity=".4" '
                        f'stroke-width="2"/>'
                    )

        for i, (x, y) in enumerate(nodes):
            color = primary if i % 2 == 0 else secondary
            elements.append(
                f'<circle cx="{x}" cy="{y}" r="8" '
                f'fill="{color}"/>'
            )

    elif motif == "web":
        elements.append(
            '<rect x="895" y="65" width="195" height="155" '
            'rx="12" fill="#FFFFFF" fill-opacity=".035" '
            f'stroke="{primary}" stroke-width="2"/>'
        )

        elements.append(
            f'<line x1="895" y1="99" x2="1090" y2="99" '
            f'stroke="{secondary}" opacity=".7"/>'
        )

        for i in range(3):
            x = 920 + i * 52
            elements.append(
                f'<rect x="{x}" y="120" width="38" height="70" '
                f'rx="5" fill="{primary}" fill-opacity=".14" '
                f'stroke="{secondary}" stroke-width="1.5"/>'
            )

    elif motif == "architecture":
        positions = [
            (900, 75), (1000, 75),
            (900, 170), (1000, 170),
        ]

        for i, (x, y) in enumerate(positions):
            elements.append(
                f'<rect x="{x}" y="{y}" width="65" height="48" '
                f'rx="8" fill="{primary}" fill-opacity=".09" '
                f'stroke="{secondary if i % 2 else primary}" '
                f'stroke-width="2"/>'
            )

        elements.append(
            f'<path d="M965 99 H1000 M965 194 H1000 '
            f'M932 123 V170 M1032 123 V170" '
            f'stroke="{primary}" stroke-width="2" fill="none"/>'
        )

    elif motif == "cloud":
        elements.append(
            f'<path d="M905 165 '
            f'A35 35 0 0 1 944 120 '
            f'A55 55 0 0 1 1040 110 '
            f'A40 40 0 0 1 1070 170 '
            f'Z" fill="{primary}" fill-opacity=".10" '
            f'stroke="{secondary}" stroke-width="3"/>'
        )

        elements.append(
            f'<path d="M990 140 V185 M970 165 L990 185 L1010 165" '
            f'fill="none" stroke="{secondary}" '
            f'stroke-width="3" stroke-linecap="round"/>'
        )

    elif motif == "data":
        for i in range(7):
            x = 900 + i * 27
            height = rng.randint(45, 130)

            elements.append(
                f'<rect x="{x}" y="{220-height}" width="15" '
                f'height="{height}" rx="4" fill="{primary}" '
                f'opacity=".8"/>'
            )

        elements.append(
            f'<path d="M890 225 H1095" '
            f'stroke="{secondary}" opacity=".6"/>'
        )

    elif motif == "productivity":
        for i, radius in enumerate([35, 65, 95]):
            color = primary if i % 2 == 0 else secondary

            elements.append(
                f'<circle cx="990" cy="145" r="{radius}" '
                f'fill="none" stroke="{color}" '
                f'stroke-width="2" opacity=".65"/>'
            )

        elements.append(
            f'<circle cx="990" cy="145" r="9" fill="{primary}"/>'
        )

    else:
        for i in range(6):
            x = 915 + i * 32
            y = 95 + (i % 2) * 65

            elements.append(
                f'<circle cx="{x}" cy="{y}" r="15" '
                f'fill="{primary}" fill-opacity=".12" '
                f'stroke="{secondary}" stroke-width="2"/>'
            )

    return "\n".join(elements)


def create_svg(insight, published_at, token):
    """
    Create a responsive, dynamically sized editorial SVG.

    Reserved zones:
    Header       50-130
    Title        170 onwards
    Illustration upper-right
    Explanation  below title divider
    Takeaway     dynamic panel
    Footer       bottom metadata
    """

    import hashlib
    import html
    import random
    import xml.etree.ElementTree as ET

    digest = hashlib.sha256(token.encode()).hexdigest()
    seed = int(digest[:12], 16)
    rng = random.Random(seed)

    theme = THEMES[insight["category"]]
    palette = theme["colors"][seed % len(theme["colors"])]

    primary, secondary, background = palette

    # Layout constants.
    WIDTH = 1200
    LEFT = 76
    RIGHT = 1124
    CONTENT_WIDTH = RIGHT - LEFT

    # Content wrapping.
    title_lines = wrap_text(insight["title"], 36)
    body_lines = wrap_text(insight["explanation"], 98)
    takeaway_lines = wrap_text(insight["takeaway"], 88)

    if len(title_lines) > 4:
        raise RuntimeError("Title is too long for the SVG layout.")

    if len(body_lines) > 14:
        raise RuntimeError("Explanation exceeds the SVG layout limit.")

    if len(takeaway_lines) > 5:
        raise RuntimeError("Takeaway exceeds the SVG layout limit.")

    # Typography.
    title_size = 39 if len(title_lines) <= 2 else 34
    title_spacing = 49
    body_size = 18
    body_spacing = 32
    takeaway_size = 17
    takeaway_spacing = 28

    # Header.
    header_y = 70
    category_y = 123
    title_y = 204

    title_end = title_y + (len(title_lines)-1) * title_spacing

    divider_y = title_end + 45
    body_y = divider_y + 60

    body_end = body_y + (len(body_lines)-1) * body_spacing

    panel_y = body_end + 58
    panel_padding_top = 27
    panel_padding_bottom = 27

    panel_height = (
        panel_padding_top
        + 20
        + 17
        + len(takeaway_lines) * takeaway_spacing
        + panel_padding_bottom
    )

    footer_divider_y = panel_y + panel_height + 42
    footer_y = footer_divider_y + 43

    HEIGHT = footer_y + 68

    # Illustration stays in its own region.
    artwork = create_artwork(
        theme["motif"],
        primary,
        secondary,
        rng,
    )

    # Restrained background details.
    particles = []

    for _ in range(15):
        x = rng.randint(850, 1150)
        y = rng.randint(25, 255)
        radius = rng.randint(1, 2)

        particles.append(
            f'<circle cx="{x}" cy="{y}" r="{radius}" '
            f'fill="{primary}" opacity=".4"/>'
        )

    # SVG text.
    title_markup = render_lines(
        title_lines,
        LEFT,
        title_y,
        title_size,
        "#F8FAFC",
        700,
        title_spacing,
    )

    body_markup = render_lines(
        body_lines,
        LEFT,
        body_y,
        body_size,
        "#CBD5E1",
        400,
        body_spacing,
    )

    takeaway_markup = render_lines(
        takeaway_lines,
        LEFT + 25,
        panel_y + panel_padding_top + 60,
        takeaway_size,
        "#F8FAFC",
        600,
        takeaway_spacing,
    )

    category = html.escape(insight["category"].upper())
    timestamp = html.escape(published_at)

    # Unique decorative geometry.
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
width="{WIDTH}"
height="{HEIGHT}"
viewBox="0 0 {WIDTH} {HEIGHT}"
preserveAspectRatio="xMidYMid meet"
role="img"
aria-labelledby="svg-title svg-description">

<title id="svg-title">{html.escape(insight["title"])}</title>

<desc id="svg-description">
{html.escape(insight["explanation"])}
</desc>

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
    <stop offset="0%" stop-color="{primary}" stop-opacity=".12"/>
    <stop offset="100%" stop-color="{primary}" stop-opacity="0"/>
  </radialGradient>

  <pattern id="grid" width="32" height="32"
    patternUnits="userSpaceOnUse">
    <circle cx="1" cy="1" r="1"
      fill="#94A3B8" opacity=".10"/>
  </pattern>
</defs>

<style>
  .particle {{
    animation: float 8s ease-in-out infinite alternate;
  }}

  .artwork {{
    transform-box: fill-box;
    transform-origin: center;
    animation: breathe 6s ease-in-out infinite alternate;
  }}

  .accent-line {{
    animation: shimmer 5s ease-in-out infinite alternate;
  }}

  @keyframes float {{
    from {{ opacity: .15; transform: translateY(0); }}
    to {{ opacity: .45; transform: translateY(8px); }}
  }}

  @keyframes breathe {{
    from {{ opacity: .85; }}
    to {{ opacity: 1; }}
  }}

  @keyframes shimmer {{
    from {{ opacity: .7; }}
    to {{ opacity: 1; }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    .particle, .artwork, .accent-line {{
      animation: none;
    }}
  }}
</style>

<!-- Canvas -->
<rect width="{WIDTH}" height="{HEIGHT}"
  rx="22" fill="url(#background)"/>

<rect width="{WIDTH}" height="{HEIGHT}"
  rx="22" fill="url(#grid)"/>

<!-- Left accent -->
<rect x="0" y="0" width="7" height="{HEIGHT}"
  rx="3" fill="url(#accent)"/>

<!-- Restrained illustration glow -->
<circle cx="990" cy="145" r="190"
  fill="url(#glow)"/>

<!-- Background particles: reserved upper-right only -->
<g class="particle">
  {"".join(particles)}
</g>

<!-- Topic illustration -->
<g class="artwork">
  {artwork}
</g>

<!-- Header -->
<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="{LEFT}" y="{header_y}"
    font-size="20"
    font-weight="750"
    letter-spacing="1.4"
    fill="#F8FAFC">
    JUNAEID
    <tspan fill="{primary}"> / </tspan>
    TECH JOURNAL
  </text>

  <rect x="{LEFT}" y="104"
    width="5" height="23" rx="2"
    fill="url(#accent)"/>

  <text x="{LEFT+19}" y="{category_y}"
    font-size="13"
    font-weight="700"
    letter-spacing="1.5"
    fill="{primary}">
    {category}
  </text>

  <!-- Title -->
  {title_markup}

  <!-- Divider -->
  <line x1="{LEFT}" y1="{divider_y}"
    x2="{RIGHT}" y2="{divider_y}"
    stroke="#64748B"
    stroke-width="1"
    opacity=".4"/>

  <!-- Full explanation -->
  {body_markup}

  <!-- Takeaway panel -->
  <rect x="{LEFT}" y="{panel_y}"
    width="{CONTENT_WIDTH}"
    height="{panel_height}"
    rx="14"
    fill="#FFFFFF"
    fill-opacity=".065"/>

  <rect x="{LEFT}" y="{panel_y}"
    width="4"
    height="{panel_height}"
    rx="2"
    fill="url(#accent)"
    class="accent-line"/>

  <text x="{LEFT+25}"
    y="{panel_y+panel_padding_top+13}"
    font-size="12"
    font-weight="750"
    letter-spacing="1.4"
    fill="{primary}">
    KEY TAKEAWAY
  </text>

  {takeaway_markup}

  <!-- Footer -->
  <line x1="{LEFT}" y1="{footer_divider_y}"
    x2="{RIGHT}" y2="{footer_divider_y}"
    stroke="#64748B"
    opacity=".4"/>

  <text x="{LEFT}" y="{footer_y}"
    font-size="13"
    font-weight="700"
    fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="{RIGHT}" y="{footer_y}"
    text-anchor="end"
    font-size="12"
    fill="#94A3B8">
    UPDATED {timestamp} · DHAKA
  </text>

  <text x="{LEFT}" y="{footer_y+27}"
    font-size="10"
    letter-spacing="1.2"
    fill="{primary}">
    AI-ASSISTED TECHNOLOGY JOURNAL
  </text>

</g>
</svg>'''

    # Strict structural and content validation.
    root = ET.fromstring(svg)

    if root.attrib["width"] != str(WIDTH):
        raise RuntimeError("SVG width validation failed.")

    if int(root.attrib["height"]) != HEIGHT:
        raise RuntimeError("SVG height validation failed.")

    text_nodes = [
        element.text or ""
        for element in root.iter()
        if element.tag.endswith("text")
    ]

    rendered = " ".join(text_nodes)

    for line in title_lines + body_lines + takeaway_lines:
        if line not in rendered:
            raise RuntimeError(
                f"SVG text missing or truncated: {line}"
            )

    if "UPDATED" not in rendered:
        raise RuntimeError("SVG timestamp is missing.")

    if "CREATED BY MUHAMMAD JUNAEID" not in rendered:
        raise RuntimeError("Author credit is missing.")

    return svg

    # Validate XML and confirm every content field is present.
    root = ET.fromstring(svg)

    if root.attrib.get("width") != "1200":
        raise RuntimeError("Incorrect SVG width.")

    if int(root.attrib["height"]) != height:
        raise RuntimeError("Incorrect SVG height.")

    rendered_text = " ".join(" ".join(root.itertext()).split())

    if insight["explanation"] not in rendered_text:
        raise RuntimeError("SVG explanation was not fully rendered.")

    if insight["takeaway"] not in rendered_text:
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
