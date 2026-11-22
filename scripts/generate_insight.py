"""
JUNAEID / TECH JOURNAL
Automated technology insight publisher.

Features:
- Gemini REST API using Python standard library only.
- Three-attempt retry system with exponential backoff.
- Dhaka timezone.
- Strict JSON and content validation.
- Atomic file writes.
- Automatic README publication.
- 4K generative animated SVG artwork.
- Subject-aware, deterministic visual compositions.
- Dynamic color palettes and lighting.
- Eight procedural artwork families.
- Full explanation and takeaway preservation.
- No category-to-icon artwork templates.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET


# ============================================================
# 1. CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

README_FILE = ROOT / "README.md"
DATA_FILE = ROOT / "data" / "insight.json"
SVG_FILE = ROOT / "assets" / "ai-tech-insight.svg"

TIMEZONE = ZoneInfo("Asia/Dhaka")

MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite",
)

API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{MODEL}:generateContent"
)

REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 3
MAX_SVG_BYTES = 2_000_000

MAX_TITLE_LENGTH = 100
MAX_EXPLANATION_LENGTH = 500
MAX_TAKEAWAY_LENGTH = 200

README_START = "<!-- INSIGHT:START -->"
README_END = "<!-- INSIGHT:END -->"

ALLOWED_CATEGORIES = {
    "Artificial Intelligence",
    "Web Development",
    "Cybersecurity",
    "Software Engineering",
    "Emerging Technology",
    "Developer Productivity",
    "Cloud Computing",
    "Data and Privacy",
}

BLOCKED_PATTERNS = [
    r"<[^>]+>",
    r"https?://\S+",
    r"www\.\S+",
    r"[\x00-\x08\x0b\x0c\x0e-\x1f]",
]


# ============================================================
# 2. LOGGING
# ============================================================

def log(message: str) -> None:
    print(f"[JUNAEID JOURNAL] {message}", flush=True)


def fail(message: str) -> None:
    raise RuntimeError(message)


# ============================================================
# 3. TIME MANAGEMENT
# ============================================================

def get_local_time() -> datetime:
    return datetime.now(TIMEZONE)


def get_publication_token(now: datetime) -> str:
    """
    Each workflow execution receives its own visual identity.

    Different publications on the same day do not reuse
    identical artwork merely because their dates match.
    """

    value = now.isoformat(timespec="seconds")

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


# ============================================================
# 4. CONTENT VALIDATION
# ============================================================

def clean_text(value: Any, field: str, maximum: int) -> str:

    if not isinstance(value, str):
        fail(f"{field} must be a string.")

    value = value.strip()

    if not value:
        fail(f"{field} cannot be empty.")

    if len(value) > maximum:
        fail(f"{field} exceeds {maximum} characters.")

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, value, re.IGNORECASE):
            fail(f"{field} contains prohibited content.")

    if any(ord(char) < 32 for char in value):
        fail(f"{field} contains control characters.")

    return value


def validate_insight(raw: Any, now: datetime) -> dict:

    if not isinstance(raw, dict):
        fail("Gemini response must be a JSON object.")

    expected = {
        "title",
        "category",
        "explanation",
        "takeaway",
    }

    if set(raw.keys()) != expected:
        fail("Unexpected or missing JSON fields.")

    title = clean_text(
        raw["title"],
        "title",
        MAX_TITLE_LENGTH,
    )

    category = clean_text(
        raw["category"],
        "category",
        50,
    )

    explanation = clean_text(
        raw["explanation"],
        "explanation",
        MAX_EXPLANATION_LENGTH,
    )

    takeaway = clean_text(
        raw["takeaway"],
        "takeaway",
        MAX_TAKEAWAY_LENGTH,
    )

    if category not in ALLOWED_CATEGORIES:
        fail(f"Unapproved category: {category}")

    if len(explanation.split()) < 35:
        fail("Explanation is too short.")

    if len(takeaway.split()) < 8:
        fail("Takeaway is too short.")

    return {
        "title": title,
        "category": category,
        "explanation": explanation,
        "takeaway": takeaway,
        "date": now.isoformat(timespec="seconds"),
    }


# ============================================================
# 5. GEMINI API
# ============================================================

def build_prompt() -> str:

    return """
You are the senior technical editor of JUNAEID / TECH JOURNAL.

Produce one original, genuinely useful technology insight.

Editorial requirements:

1. Select a specific technical concept, mechanism,
   engineering technique, protocol, architecture, or discovery.

2. Avoid generic topics such as "AI is changing the world".

3. Rotate across different technology disciplines.

4. Do not repeat obvious introductory topics.

5. Prefer useful engineering knowledge that developers
   can understand and apply.

6. Explain what the technology is, how it works,
   why it matters, and one practical use case.

7. The explanation must contain approximately
   70-100 words.

8. The takeaway must communicate one actionable
   engineering lesson.

9. Use accurate technical terminology.

10. Do not invent benchmarks, research findings,
    statistics, or product capabilities.

11. Write in clear, professional English.

12. Return JSON only.

Required JSON structure:

{
  "title": "Specific technical topic",
  "category": "One approved category",
  "explanation": "Complete technical explanation.",
  "takeaway": "Practical engineering lesson."
}

Approved categories:

- Artificial Intelligence
- Web Development
- Cybersecurity
- Software Engineering
- Emerging Technology
- Developer Productivity
- Cloud Computing
- Data and Privacy

Do not include Markdown fences.
Do not include URLs.
Do not include HTML.
""".strip()


def extract_json(text: str) -> dict:

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        result = json.loads(text)

    except json.JSONDecodeError:

        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            fail("Gemini did not return valid JSON.")

        try:
            result = json.loads(text[start:end + 1])

        except json.JSONDecodeError as exc:
            fail(f"Invalid Gemini JSON: {exc}")

    if not isinstance(result, dict):
        fail("Gemini response is not an object.")

    return result


def request_gemini(api_key: str) -> dict:

    payload = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "You are a precise, technically "
                        "accurate technology editor. "
                        "Return only valid JSON."
                    )
                }
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": build_prompt()
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.9,
            "responseMimeType": "application/json",
            "maxOutputTokens": 1024,
        },
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):

        try:

            log(
                f"Requesting Gemini "
                f"({attempt}/{MAX_ATTEMPTS})..."
            )

            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT,
            ) as response:

                response_data = json.loads(
                    response.read().decode("utf-8")
                )

            candidates = response_data.get(
                "candidates",
                [],
            )

            if not candidates:
                fail("Gemini returned no candidates.")

            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )

            text_parts = [
                part["text"]
                for part in parts
                if isinstance(part, dict)
                and isinstance(part.get("text"), str)
            ]

            if not text_parts:
                fail("Gemini returned no text.")

            return extract_json(
                "\n".join(text_parts)
            )

        except urllib.error.HTTPError as exc:

            retryable = exc.code in {
                408, 429, 500, 502, 503, 504
            }

            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            if not retryable or attempt == MAX_ATTEMPTS:
                fail(
                    f"Gemini HTTP {exc.code}: "
                    f"{error_body[:500]}"
                )

            log(f"Temporary API error: HTTP {exc.code}")

        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:

            if attempt == MAX_ATTEMPTS:
                fail(f"Gemini network failure: {exc}")

            log(f"Network issue: {exc}")

        except RuntimeError:
            raise

        except Exception as exc:

            fail(f"Gemini request failed: {exc}")

        delay = min(4 * (2 ** (attempt - 1)), 20)
        delay += random.uniform(0, 2)

        log(f"Retrying in {delay:.1f} seconds...")
        time.sleep(delay)

    fail("Gemini request exhausted all attempts.")


# ============================================================
# 6. CONTENT-AWARE ART DIRECTION
# ============================================================

SCENES = [
    "orbital",
    "network",
    "flow",
    "wave",
    "prism",
    "layers",
    "spectrum",
    "isometric",
]


SEMANTIC_RULES = [
    (
        [
            "kernel", "ebpf", "telemetry",
            "observability", "tracing",
            "system call", "monitoring",
        ],
        "network",
    ),
    (
        [
            "database", "sql", "query",
            "index", "transaction", "storage",
        ],
        "layers",
    ),
    (
        [
            "embedding", "rag", "agent",
            "neural", "reasoning", "transformer",
        ],
        "orbital",
    ),
    (
        [
            "api", "request", "response",
            "gateway", "protocol", "rate limit",
        ],
        "flow",
    ),
    (
        [
            "identity", "encryption", "cryptography",
            "authentication", "zero trust",
        ],
        "prism",
    ),
    (
        [
            "distributed", "microservice",
            "cloud", "cluster", "serverless",
        ],
        "isometric",
    ),
    (
        [
            "performance", "latency", "optimization",
            "throughput", "rendering", "benchmark",
        ],
        "wave",
    ),
    (
        [
            "quantum", "frequency", "spectrum",
            "signal", "wireless", "interference",
        ],
        "spectrum",
    ),
]


def get_art_direction(
    insight: dict,
    token: str,
) -> dict:

    content = (
        insight["title"] + " " +
        insight["explanation"]
    ).lower()

    digest = hashlib.sha256(
        (token + content).encode("utf-8")
    ).hexdigest()

    seed = int(digest[:16], 16)

    rng = random.Random(seed)

    scores = {}

    for keywords, scene in SEMANTIC_RULES:

        score = sum(
            1 for keyword in keywords
            if keyword in content
        )

        if score:
            scores[scene] = score

    if scores:

        maximum = max(scores.values())

        candidates = sorted(
            scene
            for scene, score in scores.items()
            if score == maximum
        )

        scene = candidates[seed % len(candidates)]

    else:
        scene = rng.choice(SCENES)

    hue = seed % 360

    palette = [
        f"hsl({hue}, 96%, 68%)",
        f"hsl({(hue + 85) % 360}, 95%, 70%)",
        f"hsl({(hue + 190) % 360}, 90%, 67%)",
    ]

    return {
        "scene": scene,
        "seed": seed,
        "rng": rng,
        "palette": palette,
    }


# ============================================================
# 7. GENERATIVE ARTWORK
# ============================================================

def scene_orbital(rng, p, s, t):

    output = []

    for index, radius in enumerate(
        [180, 280, 390, 500]
    ):

        color = [p, s, t][index % 3]

        output.append(
            f'<circle cx="600" cy="620" r="{radius}" '
            f'fill="none" stroke="{color}" '
            f'stroke-width="3" opacity=".4" '
            f'stroke-dasharray="12 18" '
            f'class="ambient-motion"/>'
        )

    for i in range(28):

        angle = math.radians(i * 360 / 28)

        radius = 200 + (i % 4) * 85

        x = 600 + math.cos(angle) * radius
        y = 620 + math.sin(angle) * radius

        color = [p, s, t][i % 3]

        output.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" '
            f'r="{7 + i % 6}" fill="{color}" '
            f'class="art-pulse" '
            f'style="animation-delay:-{i*.17:.2f}s"/>'
        )

    output.append(
        f'<circle cx="600" cy="620" r="115" '
        f'fill="url(#artGlow)" stroke="{s}" '
        f'stroke-width="4"/>'
    )

    return "\n".join(output)


def scene_network(rng, p, s, t):

    output = []
    nodes = []

    for row in range(5):

        for col in range(5):

            x = 180 + col * 205 + rng.randint(-35, 35)
            y = 210 + row * 190 + rng.randint(-35, 35)

            nodes.append((x, y))

    for i, (x1, y1) in enumerate(nodes):

        nearest = sorted(
            nodes[:i] + nodes[i + 1:],
            key=lambda item: (
                (item[0] - x1) ** 2 +
                (item[1] - y1) ** 2
            ),
        )[:2]

        for x2, y2 in nearest:

            output.append(
                f'<line x1="{x1}" y1="{y1}" '
                f'x2="{x2}" y2="{y2}" '
                f'stroke="{p}" stroke-width="3" '
                f'opacity=".38" class="data-flow"/>'
            )

    for i, (x, y) in enumerate(nodes):

        color = [p, s, t][i % 3]
        radius = 10 if i % 4 else 19

        output.append(
            f'<circle cx="{x}" cy="{y}" '
            f'r="{radius + 18}" fill="{color}" '
            f'fill-opacity=".10"/>'
        )

        output.append(
            f'<circle cx="{x}" cy="{y}" '
            f'r="{radius}" fill="{color}" '
            f'class="art-pulse"/>'
        )

    return "\n".join(output)


def scene_flow(rng, p, s, t):

    output = []

    positions = [
        (120, 250),
        (430, 150),
        (750, 320),
        (400, 660),
        (800, 850),
    ]

    for i in range(len(positions) - 1):

        x1, y1 = positions[i]
        x2, y2 = positions[i + 1]

        output.append(
            f'<path d="M{x1+75} {y1+55} '
            f'C{x1+180} {y1+10} '
            f'{x2-100} {y2+100} '
            f'{x2} {y2+65}" '
            f'fill="none" stroke="{s}" '
            f'stroke-width="7" opacity=".65" '
            f'stroke-dasharray="15 18" '
            f'class="data-flow"/>'
        )

    for i, (x, y) in enumerate(positions):

        color = [p, s, t][i % 3]

        output.append(
            f'<rect x="{x}" y="{y}" '
            f'width="150" height="110" rx="22" '
            f'fill="{color}" fill-opacity=".12" '
            f'stroke="{color}" stroke-width="4"/>'
        )

        output.append(
            f'<circle cx="{x+75}" cy="{y+55}" '
            f'r="18" fill="{color}"/>'
        )

    return "\n".join(output)


def scene_wave(rng, p, s, t):

    output = []

    for i in range(12):

        points = []

        for step in range(51):

            x = 25 + step * 23

            y = (
                650 +
                math.sin(step * .22 + i * .35) *
                (75 + i * 13) +
                i * 17
            )

            points.append(f"{x:.1f},{y:.1f}")

        color = [p, s, t][i % 3]

        output.append(
            f'<polyline points="{" ".join(points)}" '
            f'fill="none" stroke="{color}" '
            f'stroke-width="{3 if i % 3 else 6}" '
            f'opacity=".8" class="wave-motion"/>'
        )

    return "\n".join(output)


def scene_prism(rng, p, s, t):

    output = []

    for i in range(10):

        x = rng.randint(140, 950)
        y = rng.randint(150, 950)
        radius = rng.randint(65, 170)

        points = []

        for j in range(6):

            angle = math.radians(
                j * 60 + rng.randint(-12, 12)
            )

            px = x + math.cos(angle) * radius
            py = y + math.sin(angle) * radius

            points.append(f"{px:.1f},{py:.1f}")

        color = [p, s, t][i % 3]

        output.append(
            f'<polygon points="{" ".join(points)}" '
            f'fill="{color}" fill-opacity=".08" '
            f'stroke="{color}" stroke-width="3" '
            f'class="art-pulse"/>'
        )

    return "\n".join(output)


def scene_layers(rng, p, s, t):

    output = []

    for i in range(9):

        x = 150 + i * 25
        y = 180 + i * 100
        width = 880 - i * 65

        color = [p, s, t][i % 3]

        output.append(
            f'<path d="M{x} {y} '
            f'L{x+width/2} {y-80} '
            f'L{x+width} {y} '
            f'L{x+width/2} {y+80} Z" '
            f'fill="{color}" fill-opacity=".12" '
            f'stroke="{color}" stroke-width="3"/>'
        )

    return "\n".join(output)


def scene_spectrum(rng, p, s, t):

    output = []

    for i in range(42):

        x = 90 + i * 23
        height = rng.randint(80, 760)
        y = 1080 - height

        color = [p, s, t][i % 3]

        output.append(
            f'<rect x="{x}" y="{y}" '
            f'width="13" height="{height}" '
            f'rx="6" fill="{color}" opacity=".8" '
            f'class="art-pulse" '
            f'style="animation-delay:-{i*.11:.2f}s"/>'
        )

    return "\n".join(output)


def scene_isometric(rng, p, s, t):

    output = []

    for row in range(5):

        for col in range(5):

            x = 140 + col * 180 + row * 75
            y = 200 + row * 145 - col * 45

            color = [p, s, t][(row + col) % 3]

            output.append(
                f'<path d="M{x} {y} '
                f'l65 -38 l65 38 l-65 38 Z" '
                f'fill="{color}" fill-opacity=".12" '
                f'stroke="{color}" stroke-width="3"/>'
            )

            output.append(
                f'<path d="M{x} {y} v90 '
                f'l65 38 v-90" fill="none" '
                f'stroke="{color}" stroke-width="2" '
                f'opacity=".6"/>'
            )

    return "\n".join(output)


SCENE_FUNCTIONS = {
    "orbital": scene_orbital,
    "network": scene_network,
    "flow": scene_flow,
    "wave": scene_wave,
    "prism": scene_prism,
    "layers": scene_layers,
    "spectrum": scene_spectrum,
    "isometric": scene_isometric,
}


# ============================================================
# 8. TYPOGRAPHY AND CONTENT LAYOUT
# ============================================================

def wrap_text(text: str, max_chars: int) -> list[str]:

    words = text.split()

    lines = []
    current = ""

    for word in words:

        candidate = f"{current} {word}".strip()

        if len(candidate) > max_chars and current:

            lines.append(current)
            current = word

        else:
            current = candidate

    if current:
        lines.append(current)

    return lines


def render_lines(
    lines: list[str],
    x: int,
    y: int,
    size: int,
    spacing: int,
    color: str,
    weight: int = 400,
) -> str:

    output = []

    for index, line in enumerate(lines):

        output.append(
            f'<text x="{x}" y="{y + index * spacing}" '
            f'font-size="{size}" fill="{color}" '
            f'font-weight="{weight}">'
            f'{html.escape(line)}'
            f'</text>'
        )

    return "\n".join(output)


# ============================================================
# 9. PREMIUM 4K SVG GENERATOR
# ============================================================

def create_svg(
    insight: dict,
    published_at: str,
    token: str,
) -> str:

    log("Designing content-aware artwork...")

    direction = get_art_direction(insight, token)

    scene = direction["scene"]
    rng = direction["rng"]

    primary, secondary, tertiary = direction["palette"]

    art = SCENE_FUNCTIONS[scene](
        rng,
        primary,
        secondary,
        tertiary,
    )

    # 4K canvas.
    WIDTH = 3840
    HEIGHT = 2160

    LEFT = 220
    RIGHT = 2150

    # Text wrapping.
    title_lines = wrap_text(insight["title"], 29)
    body_lines = wrap_text(insight["explanation"], 73)
    takeaway_lines = wrap_text(insight["takeaway"], 65)

    if len(title_lines) > 3:
        fail("Title exceeds SVG layout capacity.")

    if len(body_lines) > 14:
        fail("Explanation exceeds SVG layout capacity.")

    if len(takeaway_lines) > 4:
        fail("Takeaway exceeds SVG layout capacity.")

    # Dynamic editorial layout.
    title_y = 480
    title_size = 90
    title_spacing = 105

    title_end = (
        title_y +
        (len(title_lines) - 1) * title_spacing
    )

    divider_y = title_end + 70
    body_y = divider_y + 70

    body_size = 39
    body_spacing = 55

    body_end = (
        body_y +
        (len(body_lines) - 1) * body_spacing
    )

    panel_y = body_end + 70

    panel_height = (
        145 +
        len(takeaway_lines) * 52
    )

    if panel_y + panel_height > 1980:
        fail("Content cannot fit safely in SVG canvas.")

    footer_y = 2075

    title_markup = render_lines(
        title_lines,
        LEFT,
        title_y,
        title_size,
        title_spacing,
        "#FFFFFF",
        750,
    )

    body_markup = render_lines(
        body_lines,
        LEFT,
        body_y,
        body_size,
        body_spacing,
        "#D8E3F5",
        400,
    )

    takeaway_markup = render_lines(
        takeaway_lines,
        LEFT + 45,
        panel_y + 110,
        38,
        52,
        "#FFFFFF",
        650,
    )

    # Subject-specific particles.
    particles = []

    for i in range(75):

        x = rng.randint(40, WIDTH - 40)
        y = rng.randint(40, HEIGHT - 40)
        radius = rng.choice([2, 3, 4, 6])

        color = [primary, secondary, tertiary][i % 3]

        delay = rng.randint(0, 12)

        particles.append(
            f'<circle cx="{x}" cy="{y}" '
            f'r="{radius}" fill="{color}" '
            f'opacity=".35" class="particle" '
            f'style="animation-delay:-{delay}s"/>'
        )

    category = html.escape(
        insight["category"].upper()
    )

    timestamp = html.escape(published_at)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
width="{WIDTH}" height="{HEIGHT}"
viewBox="0 0 {WIDTH} {HEIGHT}"
role="img" aria-labelledby="svg-title svg-desc">

<title id="svg-title">
{html.escape(insight["title"])}
</title>

<desc id="svg-desc">
{html.escape(insight["explanation"])}
</desc>

<defs>

  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#080C18"/>
    <stop offset="50%" stop-color="#111B31"/>
    <stop offset="100%" stop-color="#17102C"/>
  </linearGradient>

  <linearGradient id="metal" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{primary}"/>
    <stop offset="48%" stop-color="{secondary}"/>
    <stop offset="100%" stop-color="{tertiary}"/>
  </linearGradient>

  <radialGradient id="artGlow">
    <stop offset="0%" stop-color="{primary}" stop-opacity=".55"/>
    <stop offset="45%" stop-color="{secondary}" stop-opacity=".16"/>
    <stop offset="100%" stop-color="#080C18" stop-opacity="0"/>
  </radialGradient>

  <radialGradient id="ambient">
    <stop offset="0%" stop-color="{secondary}" stop-opacity=".16"/>
    <stop offset="100%" stop-color="#080C18" stop-opacity="0"/>
  </radialGradient>

  <pattern id="microGrid" width="60" height="60"
    patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="2"
      fill="#A5B4FC" opacity=".15"/>
  </pattern>

  <clipPath id="canvasClip">
    <rect width="{WIDTH}" height="{HEIGHT}" rx="48"/>
  </clipPath>

</defs>

<style>

  .particle {{
    animation: particleFloat 9s ease-in-out infinite alternate;
  }}

  .art-pulse {{
    transform-box: fill-box;
    transform-origin: center;
    animation: artPulse 5s ease-in-out infinite alternate;
  }}

  .data-flow {{
    stroke-dasharray: 18 28;
    animation: dataFlow 8s linear infinite;
  }}

  .wave-motion {{
    animation: waveDrift 9s ease-in-out infinite alternate;
  }}

  .ambient-motion {{
    transform-box: fill-box;
    transform-origin: center;
    animation: ambientMotion 14s ease-in-out infinite alternate;
  }}

  @keyframes particleFloat {{
    0% {{
      transform: translateY(0);
      opacity: .12;
    }}
    100% {{
      transform: translateY(-35px);
      opacity: .65;
    }}
  }}

  @keyframes artPulse {{
    0% {{ opacity: .55; }}
    100% {{ opacity: 1; }}
  }}

  @keyframes dataFlow {{
    to {{ stroke-dashoffset: -184; }}
  }}

  @keyframes waveDrift {{
    from {{ transform: translateY(-12px); }}
    to {{ transform: translateY(12px); }}
  }}

  @keyframes ambientMotion {{
    from {{
      transform: scale(.95) rotate(-4deg);
    }}
    to {{
      transform: scale(1.08) rotate(4deg);
    }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    .particle,
    .art-pulse,
    .data-flow,
    .wave-motion,
    .ambient-motion {{
      animation: none;
    }}
  }}

</style>

<g clip-path="url(#canvasClip)">

<!-- BACKGROUND -->

<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>

<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#microGrid)"/>

<circle cx="3050" cy="880" r="1150"
  fill="url(#artGlow)" class="ambient-motion"/>

<circle cx="3400" cy="250" r="850"
  fill="url(#ambient)" class="ambient-motion"/>

<circle cx="200" cy="2100" r="850"
  fill="url(#ambient)"/>

<!-- LIGHT STREAKS -->

<path d="M2300 0 L3840 1100"
  stroke="url(#metal)" stroke-width="3" opacity=".12"/>

<path d="M2450 2160 L3840 1000"
  stroke="url(#metal)" stroke-width="3" opacity=".12"/>

<!-- PARTICLES -->

<g>
{"".join(particles)}
</g>

<!-- EDITORIAL CONTENT -->

<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="{LEFT}" y="200"
    font-size="54" font-weight="800"
    letter-spacing="5" fill="#FFFFFF">
    JUNAEID
    <tspan fill="{primary}"> / </tspan>
    TECH JOURNAL
  </text>

  <rect x="{LEFT}" y="275"
    width="9" height="47" rx="4"
    fill="url(#metal)"/>

  <text x="{LEFT+34}" y="310"
    font-size="34" font-weight="750"
    letter-spacing="4" fill="{primary}">
    {category}
  </text>

  <!-- TITLE -->

  {title_markup}

  <line x1="{LEFT}" y1="{divider_y}"
    x2="{RIGHT}" y2="{divider_y}"
    stroke="#94A3B8" opacity=".28"
    stroke-width="3"/>

  <!-- FULL EXPLANATION -->

  {body_markup}

  <!-- TAKEAWAY -->

  <rect x="{LEFT}" y="{panel_y}"
    width="1950" height="{panel_height}"
    rx="32" fill="#FFFFFF"
    fill-opacity=".055"
    stroke="#FFFFFF"
    stroke-opacity=".12"
    stroke-width="2"/>

  <rect x="{LEFT}" y="{panel_y}"
    width="9" height="{panel_height}"
    rx="4" fill="url(#metal)"/>

  <text x="{LEFT+45}" y="{panel_y+75}"
    font-size="31" font-weight="800"
    letter-spacing="4" fill="{primary}">
    KEY TAKEAWAY
  </text>

  {takeaway_markup}

  <!-- FOOTER -->

  <line x1="{LEFT}" y1="2010"
    x2="3610" y2="2010"
    stroke="#94A3B8" opacity=".25"
    stroke-width="2"/>

  <text x="{LEFT}" y="{footer_y}"
    font-size="29" font-weight="750"
    letter-spacing="2" fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="3610" y="{footer_y}"
    font-size="25" text-anchor="end"
    fill="#A5B4FC">
    {timestamp} · DHAKA
  </text>

</g>

<!-- GENERATIVE ARTWORK -->

<g transform="translate(2300 330)">

  <circle cx="600" cy="650" r="650"
    fill="url(#artGlow)"/>

  <circle cx="600" cy="650" r="570"
    fill="none" stroke="url(#metal)"
    stroke-width="3" opacity=".22"
    stroke-dasharray="12 22"
    class="ambient-motion"/>

  {art}

</g>

<!-- PUBLICATION IDENTITY -->

<text x="3610" y="2110"
  text-anchor="end"
  font-family="Inter, Segoe UI, Arial, sans-serif"
  font-size="22" font-weight="650"
  letter-spacing="4" fill="{primary}">
  DIGITAL INTELLIGENCE JOURNAL
</text>

</g>
</svg>'''

    # XML integrity.
    try:
        root = ET.fromstring(svg)

    except ET.ParseError as exc:
        fail(f"Generated SVG is invalid: {exc}")

    if root.attrib.get("width") != str(WIDTH):
        fail("SVG width validation failed.")

    if root.attrib.get("height") != str(HEIGHT):
        fail("SVG height validation failed.")

    # Verify actual text is present.
    all_text = "".join(root.itertext())

    for line in (
        title_lines +
        body_lines +
        takeaway_lines
    ):

        if line not in all_text:
            fail(f"SVG text is missing: {line}")

    if "CREATED BY MUHAMMAD JUNAEID" not in all_text:
        fail("Author credit is missing.")

    if "DIGITAL INTELLIGENCE JOURNAL" not in all_text:
        fail("Journal identity is missing.")

    if len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        fail("SVG exceeds the maximum file size.")

    log(
        f"Artwork created: {scene} composition, "
        f"{WIDTH} × {HEIGHT}"
    )

    return svg


# ============================================================
# 10. README GENERATION
# ============================================================

def markdown_escape(value: str) -> str:

    return re.sub(
        r"([\\`*_{}\[\]()#+\-.!|>])",
        r"\\\1",
        value,
    )


def build_readme_section(insight: dict) -> str:

    title = markdown_escape(insight["title"])
    category = markdown_escape(insight["category"])
    explanation = markdown_escape(insight["explanation"])
    takeaway = markdown_escape(insight["takeaway"])

    timestamp = markdown_escape(insight["date"])

    return f"""<div align="center">

### AI TECH INSIGHT OF THE DAY

**{title}**

`{category}` · `{timestamp}`

![Junaeid Tech Journal](assets/ai-tech-insight.svg)

</div>

**The insight**

{explanation}

> **KEY TAKEAWAY**
>
> {takeaway}

<sub>Created by Muhammad Junaeid · AI-assisted technology journal</sub>
"""


def update_readme(insight: dict) -> None:

    log("Updating README.md...")

    if not README_FILE.exists():
        fail("README.md does not exist.")

    content = README_FILE.read_text(
        encoding="utf-8"
    )

    if content.count(README_START) != 1:
        fail("README start marker is missing or duplicated.")

    if content.count(README_END) != 1:
        fail("README end marker is missing or duplicated.")

    start = content.index(README_START)
    end = content.index(README_END)

    if end <= start:
        fail("README markers are in the wrong order.")

    section = build_readme_section(insight)

    updated = (
        content[:start + len(README_START)]
        + "\n\n"
        + section
        + "\n"
        + content[end:]
    )

    atomic_write(
        README_FILE,
        updated,
    )


# ============================================================
# 11. SAFE FILE OPERATIONS
# ============================================================

def atomic_write(path: Path, content: str) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        path.name + ".tmp"
    )

    try:

        temporary.write_text(
            content,
            encoding="utf-8",
            newline="\n",
        )

        temporary.replace(path)

    except Exception:

        if temporary.exists():
            temporary.unlink()

        raise


def save_json(insight: dict) -> None:

    content = json.dumps(
        insight,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    atomic_write(
        DATA_FILE,
        content,
    )


def save_svg(svg: str) -> None:

    atomic_write(
        SVG_FILE,
        svg,
    )


# ============================================================
# 12. PIPELINE
# ============================================================

def check_repository() -> None:

    log("Checking repository files...")

    if not ROOT.exists():
        fail("Repository root not found.")

    if not README_FILE.exists():
        fail("README.md is missing.")

    if not README_START in README_FILE.read_text(
        encoding="utf-8"
    ):
        fail("README insight markers are missing.")

    if not README_END in README_FILE.read_text(
        encoding="utf-8"
    ):
        fail("README end marker is missing.")

    DATA_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    SVG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


def main() -> None:

    log("Starting AI Tech Journal pipeline...")

    check_repository()

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        fail("GEMINI_API_KEY environment variable is missing.")

    now = get_local_time()

    token = get_publication_token(now)

    published_at = now.strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )

    log(f"Publication time: {published_at}")

    # Generate new content on every workflow execution.
    raw = request_gemini(api_key)

    insight = validate_insight(
        raw,
        now,
    )

    log(f"Insight: {insight['title']}")
    log(f"Category: {insight['category']}")

    # Generate and validate SVG before publication.
    svg = create_svg(
        insight,
        published_at,
        token,
    )

    # Save validated content and artwork.
    save_json(insight)
    save_svg(svg)

    # Publish README last.
    update_readme(insight)

    log("Publication completed successfully.")

    log(f"JSON: {DATA_FILE.relative_to(ROOT)}")
    log(f"SVG: {SVG_FILE.relative_to(ROOT)}")
    log(f"README: {README_FILE.relative_to(ROOT)}")


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:
        log("Execution interrupted.")
        sys.exit(130)

    except Exception as exc:

        log(f"Workflow failed safely: {exc}")

        sys.exit(1)
