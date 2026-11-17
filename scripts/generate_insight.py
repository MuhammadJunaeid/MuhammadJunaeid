
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

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# ==================================================
# BRAND & CONFIGURATION
# ==================================================

OWNER = "Muhammad Junaeid"
PROJECT = "AI Tech Insight"
MODEL = "gemini-3.5-flash-lite"

TIMEZONE = ZoneInfo("Asia/Dhaka")

ROOT = Path(__file__).resolve().parent.parent

README = ROOT / "README.md"
DATA = ROOT / "data" / "insight.json"
SVG = ROOT / "assets" / "ai-tech-insight.svg"

START = "<!-- INSIGHT:START -->"
END = "<!-- INSIGHT:END -->"

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

FIELDS = {"title", "category", "explanation", "takeaway"}


# ==================================================
# FILE UTILITIES
# ==================================================

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


def today():
    return datetime.now(TIMEZONE).date().isoformat()


def read_history():
    if not DATA.exists():
        return {}

    try:
        result = json.loads(DATA.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise RuntimeError(
            "insight.json is invalid. Repair the JSON file."
        ) from None

    if not isinstance(result, dict):
        raise RuntimeError("Insight history must be a JSON object.")

    return result


def read_readme():
    if not README.is_file():
        raise RuntimeError("README.md does not exist.")

    content = README.read_text(encoding="utf-8")

    if content.count(START) != 1 or content.count(END) != 1:
        raise RuntimeError(
            "README must contain exactly one pair of insight markers."
        )

    if content.index(START) >= content.index(END):
        raise RuntimeError("README markers are incorrectly ordered.")

    return content


# ==================================================
# VALIDATION
# ==================================================

def clean_text(value, field, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")

    value = " ".join(value.split()).strip()

    if not value or len(value) > maximum:
        raise ValueError(f"Invalid {field} length.")

    if re.search(r"<[^>]*>", value):
        raise ValueError(f"HTML is not allowed in {field}.")

    if re.search(r"https?://|www\.", value, re.I):
        raise ValueError(f"URLs are not allowed in {field}.")

    if any(ord(char) < 32 for char in value):
        raise ValueError(f"Control characters in {field}.")

    return value


def validate_insight(data):
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("Gemini returned an unexpected JSON structure.")

    result = {
        "title": clean_text(data["title"], "title", 100),
        "category": clean_text(data["category"], "category", 40),
        "explanation": clean_text(
            data["explanation"], "explanation", 500
        ),
        "takeaway": clean_text(
            data["takeaway"], "takeaway", 200
        ),
    }

    if result["category"] not in CATEGORIES:
        raise ValueError("Unapproved category.")

    if len(result["explanation"]) < 40:
        raise ValueError("Explanation is too short.")

    if len(result["takeaway"]) < 15:
        raise ValueError("Takeaway is too short.")

    return result


def md_escape(value):
    return re.sub(
        r"([\\`*_{}\[\]()#+.!|>~-])",
        r"\\\1",
        value,
    )


# ==================================================
# GEMINI API
# ==================================================

def build_prompt(previous):
    previous_title = previous.get("title", "None")
    previous_category = previous.get("category", "None")

    return f"""
You are an expert technology educator.

Create ONE original, useful, evergreen technology insight
for a software developer's public GitHub profile.

Previous insight: {previous_title}
Previous category: {previous_category}

Select exactly one category:
{", ".join(sorted(CATEGORIES))}

Requirements:
1. Explain a specific technical concept.
2. Make it useful to developers and technology learners.
3. Use accurate, natural, beginner-friendly English.
4. Avoid repeating the previous concept.
5. Do not invent statistics, research or quotations.
6. Avoid breaking news and unsupported claims.
7. No HTML, Markdown, URLs, emojis or code blocks.
8. Title should be interesting, not clickbait.
9. Explanation should contain 2-4 meaningful sentences.
10. Takeaway should contain one practical lesson.

Return ONLY valid JSON with exactly these fields:

{{
  "title": "Insight title",
  "category": "Approved category",
  "explanation": "Technical explanation",
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
            "temperature": 0.7,
            "maxOutputTokens": 512,
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
        print(
            f"Gemini request: {attempt}/{MAX_ATTEMPTS}",
            flush=True,
        )

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
                raise RuntimeError("No Gemini response candidates.")

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
            status = error.code

            if status not in {408, 429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Gemini HTTP error: {status}"
                ) from None

            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Gemini unavailable after retries: HTTP {status}"
                ) from None

            print(f"Temporary Gemini HTTP error: {status}")

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ):
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Gemini network request failed after retries."
                ) from None

            print("Temporary network failure. Retrying...")

        except json.JSONDecodeError:
            raise RuntimeError(
                "Gemini returned malformed JSON."
            ) from None

        if attempt < MAX_ATTEMPTS:
            time.sleep(
                min(2 ** attempt, 8) + random.uniform(0, 1)
            )

    raise RuntimeError("Insight generation failed.")


# ==================================================
# SVG GRAPHIC GENERATOR
# ==================================================


def wrap_text(text, max_chars):
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


def svg_text(text, x, y, size, color,
             weight=400, max_chars=60, line_height=None):
    lines = wrap_text(text, max_chars)
    spacing = line_height or size * 1.5

    elements = []

    for index, line in enumerate(lines):
        elements.append(
            f'<text x="{x}" y="{y + index * spacing}" '
            f'font-size="{size}" fill="{color}" '
            f'font-weight="{weight}">'
            f'{html.escape(line)}'
            f'</text>'
        )

    return "\n".join(elements), len(lines)


def create_svg(insight, date):
    # Canvas and grid
    width = 1200
    height = 630
    margin = 76
    content_width = width - margin * 2

    # Dynamic typography
    title_lines = wrap_text(insight["title"], 39)

    if len(title_lines) > 2:
        raise RuntimeError("Insight title exceeds SVG layout limits.")

    explanation_lines = wrap_text(
        insight["explanation"], 100
    )

    if len(explanation_lines) > 4:
        raise RuntimeError("Explanation exceeds SVG layout limits.")

    takeaway_lines = wrap_text(
        insight["takeaway"], 95
    )

    if len(takeaway_lines) > 2:
        raise RuntimeError("Takeaway exceeds SVG layout limits.")

    def text_block(lines, x, y, size, color, weight, spacing):
        return "\n".join(
            f'<text x="{x}" y="{y + i * spacing}" '
            f'font-size="{size}" fill="{color}" '
            f'font-weight="{weight}">'
            f'{html.escape(line)}'
            f'</text>'
            for i, line in enumerate(lines)
        )

    title_y = 195
    title_spacing = 49

    title_height = len(title_lines) * title_spacing

    divider_y = title_y + title_height + 25
    explanation_y = divider_y + 65

    explanation_height = len(explanation_lines) * 29

    takeaway_top = explanation_y + explanation_height + 32
    takeaway_height = max(76, len(takeaway_lines) * 25 + 34)

    footer_divider = takeaway_top + takeaway_height + 30
    footer_y = footer_divider + 38

    # Prevent content from entering the footer.
    if footer_y > 575:
        raise RuntimeError(
            "SVG content exceeds available vertical space."
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg"
    width="{width}" height="{height}"
    viewBox="0 0 {width} {height}"
    role="img"
    aria-labelledby="card-title card-description">

    <title id="card-title">
        {html.escape(insight["title"])}
    </title>

    <desc id="card-description">
        Daily technology insight by Muhammad Junaeid.
        {html.escape(insight["explanation"])}
    </desc>

    <defs>
        <linearGradient id="background"
            x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0B1120"/>
            <stop offset="100%" stop-color="#172B46"/>
        </linearGradient>

        <linearGradient id="accent"
            x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#22D3EE"/>
            <stop offset="100%" stop-color="#818CF8"/>
        </linearGradient>

        <radialGradient id="glow">
            <stop offset="0%" stop-color="#22D3EE"
                stop-opacity=".15"/>
            <stop offset="100%" stop-color="#22D3EE"
                stop-opacity="0"/>
        </radialGradient>

        <pattern id="grid" width="32" height="32"
            patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="1"
                fill="#94A3B8" opacity=".18"/>
        </pattern>
    </defs>

    <!-- Background -->
    <rect width="1200" height="630"
        rx="20" fill="url(#background)"/>

    <rect width="1200" height="630"
        rx="20" fill="url(#grid)"/>

    <circle cx="1080" cy="110" r="280"
        fill="url(#glow)"/>

    <rect x="0" y="0" width="6" height="630"
        fill="url(#accent)"/>

    <!-- Header -->
    <g font-family="Inter, Segoe UI, Arial, sans-serif">

        <text x="{margin}" y="69"
            font-size="20"
            font-weight="700"
            fill="#F8FAFC"
            letter-spacing="1.5">
            JUNAEID
            <tspan fill="#67E8F9"> / </tspan>
            TECH JOURNAL
        </text>

        <text x="{width-margin}" y="69"
            font-size="14"
            text-anchor="end"
            fill="#94A3B8">
            {html.escape(date)}
        </text>

        <!-- Category -->
        <rect x="{margin}" y="96"
            width="7" height="22"
            rx="3" fill="#22D3EE"/>

        <text x="{margin+20}" y="113"
            font-size="14"
            font-weight="600"
            letter-spacing="1.2"
            fill="#67E8F9">
            {html.escape(insight["category"].upper())}
        </text>

        <!-- Main title -->
        {text_block(title_lines, margin, title_y,
                    39, "#F8FAFC", 700, title_spacing)}

        <!-- Divider -->
        <line x1="{margin}" y1="{divider_y}"
            x2="{width-margin}" y2="{divider_y}"
            stroke="#475569" opacity=".65"/>

        <!-- Explanation -->
        {text_block(explanation_lines, margin,
                    explanation_y, 17, "#CBD5E1", 400, 29)}

        <!-- Takeaway panel -->
        <rect x="{margin}" y="{takeaway_top}"
            width="{content_width}" height="{takeaway_height}"
            rx="12" fill="#FFFFFF" fill-opacity=".055"/>

        <rect x="{margin}" y="{takeaway_top}"
            width="4" height="{takeaway_height}"
            rx="2" fill="url(#accent)"/>

        <text x="{margin+22}" y="{takeaway_top+25}"
            font-size="11"
            font-weight="700"
            fill="#67E8F9"
            letter-spacing="1.3">
            KEY TAKEAWAY
        </text>

        {text_block(takeaway_lines, margin+22,
                    takeaway_top+51, 16, "#F8FAFC", 600, 24)}

        <!-- Footer -->
        <line x1="{margin}" y1="{footer_divider}"
            x2="{width-margin}" y2="{footer_divider}"
            stroke="#475569" opacity=".65"/>

        <text x="{margin}" y="{footer_y}"
            font-size="13"
            font-weight="600"
            fill="#E2E8F0">
            CREATED BY MUHAMMAD JUNAEID
        </text>

        <text x="{width-margin}" y="{footer_y}"
            font-size="13"
            text-anchor="end"
            fill="#67E8F9">
            POWERED BY GOOGLE GEMINI
        </text>

    </g>
    </svg>
    '''


# ==================================================
# README GENERATOR
# ==================================================

def create_readme_section(insight, date):
    return (
        "\n\n"
        "![Daily AI Tech Insight by Muhammad Junaeid]"
        "(./assets/ai-tech-insight.svg)"
        "\n\n"
    )


# ==================================================
# MAIN
# ==================================================

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY secret is missing.")

    date = today()

    print("Checking repository files...", flush=True)

    readme_content = read_readme()
    previous = read_history()

    if previous.get("date") == date:
        print(
            "Today's insight already exists. No regeneration needed.",
            flush=True,
        )
        return

    print("Generating daily AI technology insight...", flush=True)

    insight = generate_insight(api_key, previous)

    print("Generating visual SVG card...", flush=True)

    svg_content = create_svg(insight, date)

    readme_section = create_readme_section(insight, date)

    updated_readme = update_readme(
        readme_content,
        readme_section,
    )

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

    # Validate SVG before writing.
    if not svg_content.startswith("<svg "):
        raise RuntimeError("SVG generation failed.")

    # Write outputs only after all content is prepared.
    atomic_write(DATA, json_content)
    atomic_write(SVG, svg_content)
    atomic_write(README, updated_readme)

    print("===================================")
    print("AI TECH INSIGHT PUBLISHED")
    print(f"Title: {insight['title']}")
    print(f"Category: {insight['category']}")
    print(f"Date: {date}")
    print("Author: " + OWNER)
    print("Visual: assets/ai-tech-insight.svg")
    print("===================================")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"Workflow failed safely: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
