
import html
import json
import os
import random
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


# =====================================================
# CONFIGURATION
# =====================================================

OWNER = "Muhammad Junaeid"
MODEL = "gemini-3.5-flash-lite"

ROOT = Path(__file__).resolve().parent.parent

README_PATH = ROOT / "README.md"
DATA_PATH = ROOT / "data" / "insight.json"
SVG_PATH = ROOT / "assets" / "ai-tech-insight.svg"

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

TIMEZONE = ZoneInfo("Asia/Dhaka")

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


# =====================================================
# GENERAL UTILITIES
# =====================================================

def log(message):
    print(message, flush=True)


def get_today():
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
        raise RuntimeError("README.md was not found.")

    content = README_PATH.read_text(encoding="utf-8")

    if content.count(START_MARKER) != 1:
        raise RuntimeError(
            "README needs exactly one INSIGHT:START marker."
        )

    if content.count(END_MARKER) != 1:
        raise RuntimeError(
            "README needs exactly one INSIGHT:END marker."
        )

    if content.index(START_MARKER) >= content.index(END_MARKER):
        raise RuntimeError("README markers are incorrectly ordered.")

    return content


def read_history():
    if not DATA_PATH.exists():
        return {}

    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise RuntimeError(
            "Existing insight.json is invalid. Repair it first."
        ) from None

    if not isinstance(data, dict):
        raise RuntimeError("Insight JSON must contain an object.")

    return data


# =====================================================
# CONTENT VALIDATION
# =====================================================

def clean_text(value, field, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")

    value = " ".join(value.split())

    if not value or len(value) > maximum:
        raise ValueError(f"Invalid {field} length.")

    if "<" in value or ">" in value:
        raise ValueError(f"HTML characters are not allowed in {field}.")

    if "http://" in value.lower() or "https://" in value.lower():
        raise ValueError(f"URLs are not allowed in {field}.")

    if any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid control character in {field}.")

    return value


def validate_insight(data):
    if not isinstance(data, dict):
        raise ValueError("Insight must be a JSON object.")

    if set(data.keys()) != REQUIRED_FIELDS:
        raise ValueError("Insight JSON has unexpected fields.")

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
        raise ValueError("Unsupported insight category.")

    if len(result["explanation"]) < 40:
        raise ValueError("Explanation is too short.")

    if len(result["takeaway"]) < 15:
        raise ValueError("Takeaway is too short.")

    return result


# =====================================================
# GEMINI API
# =====================================================

def build_prompt(previous):
    previous_title = previous.get("title", "None")
    previous_category = previous.get("category", "None")

    return f"""
You are an expert technology educator.

Create one original, useful, evergreen technology insight
for a software developer's GitHub profile.

Previous title: {previous_title}
Previous category: {previous_category}

Choose exactly one category:
{", ".join(sorted(CATEGORIES))}

Requirements:
- Explain one specific technical concept.
- Make it useful to developers and technology learners.
- Use accurate, natural, beginner-friendly English.
- Avoid repeating the previous concept.
- Never invent statistics, studies or quotations.
- Avoid breaking news and unsupported claims.
- No HTML, URLs, emojis, Markdown or code blocks.
- Title should be interesting but not clickbait.
- Explanation: 2 to 4 meaningful sentences.
- Takeaway: one practical lesson.

Return only valid JSON with exactly these fields:
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
        log(f"Gemini request: {attempt}/{MAX_ATTEMPTS}")

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
                raise RuntimeError(
                    "Gemini returned no candidates."
                )

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
                    f"Gemini API returned HTTP {status}."
                ) from None

            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Gemini unavailable after retries: HTTP {status}."
                ) from None

            log(f"Temporary Gemini error: HTTP {status}")

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ):
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Gemini network request failed after retries."
                ) from None

            log("Temporary network failure. Retrying...")

        except json.JSONDecodeError:
            raise RuntimeError(
                "Gemini returned invalid JSON."
            ) from None

        if attempt < MAX_ATTEMPTS:
            time.sleep(
                min(2 ** attempt, 8) + random.uniform(0, 1)
            )

    raise RuntimeError("Gemini generation failed.")


# =====================================================
# SVG DESIGN
# =====================================================

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


def svg_lines(lines, x, y, size, color, weight, spacing):
    elements = []

    for index, line in enumerate(lines):
        elements.append(
            f'<text x="{x}" y="{y + index * spacing}" '
            f'font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{html.escape(line)}</text>'
        )

    return "\n".join(elements)


def create_svg(insight, date):
    margin = 76
    width = 1200
    height = 630

    title_lines = wrap_text(insight["title"], 39)
    explanation_lines = wrap_text(
        insight["explanation"], 100
    )
    takeaway_lines = wrap_text(
        insight["takeaway"], 95
    )

    if len(title_lines) > 2:
        raise RuntimeError("SVG title is too long.")

    if len(explanation_lines) > 4:
        raise RuntimeError("SVG explanation is too long.")

    if len(takeaway_lines) > 2:
        raise RuntimeError("SVG takeaway is too long.")

    title_y = 195
    title_spacing = 48

    divider_y = title_y + len(title_lines) * title_spacing + 20
    explanation_y = divider_y + 57

    explanation_height = len(explanation_lines) * 28

    panel_y = explanation_y + explanation_height + 27
    panel_height = max(76, len(takeaway_lines) * 25 + 38)

    footer_line_y = panel_y + panel_height + 25
    footer_y = footer_line_y + 37

    if footer_y > 580:
        raise RuntimeError("SVG content exceeds available space.")

    return f'''<svg xmlns="http://www.w3.org/2000/svg"
width="1200" height="630" viewBox="0 0 1200 630"
role="img" aria-labelledby="card-title card-description">

<title id="card-title">{html.escape(insight["title"])}</title>
<desc id="card-description">{html.escape(insight["explanation"])}</desc>

<defs>
  <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0B1120"/>
    <stop offset="100%" stop-color="#172B46"/>
  </linearGradient>
  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#22D3EE"/>
    <stop offset="100%" stop-color="#818CF8"/>
  </linearGradient>
  <pattern id="dots" width="30" height="30"
    patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="1" fill="#64748B" opacity=".25"/>
  </pattern>
  <radialGradient id="glow">
    <stop offset="0%" stop-color="#22D3EE" stop-opacity=".16"/>
    <stop offset="100%" stop-color="#22D3EE" stop-opacity="0"/>
  </radialGradient>
</defs>

<rect width="1200" height="630" rx="20" fill="url(#background)"/>
<rect width="1200" height="630" rx="20" fill="url(#dots)"/>
<circle cx="1080" cy="90" r="260" fill="url(#glow)"/>
<rect width="6" height="630" fill="url(#accent)"/>

<g font-family="Inter, Segoe UI, Arial, sans-serif">

  <text x="{margin}" y="69" fill="#F8FAFC"
    font-size="20" font-weight="700" letter-spacing="1.5">
    JUNAEID <tspan fill="#67E8F9">/</tspan> TECH JOURNAL
  </text>

  <text x="{width-margin}" y="69" text-anchor="end"
    font-size="14" fill="#94A3B8">
    {html.escape(date)}
  </text>

  <rect x="{margin}" y="96" width="7" height="22"
    rx="3" fill="#22D3EE"/>

  <text x="{margin+20}" y="113" font-size="14"
    font-weight="600" letter-spacing="1.2" fill="#67E8F9">
    {html.escape(insight["category"].upper())}
  </text>

  {svg_lines(title_lines, margin, title_y, 39, "#F8FAFC", 700, title_spacing)}

  <line x1="{margin}" y1="{divider_y}"
    x2="{width-margin}" y2="{divider_y}"
    stroke="#475569" opacity=".7"/>

  {svg_lines(explanation_lines, margin, explanation_y, 17, "#CBD5E1", 400, 28)}

  <rect x="{margin}" y="{panel_y}" width="1048"
    height="{panel_height}" rx="12"
    fill="#FFFFFF" fill-opacity=".055"/>

  <rect x="{margin}" y="{panel_y}" width="4"
    height="{panel_height}" rx="2" fill="url(#accent)"/>

  <text x="{margin+22}" y="{panel_y+24}"
    font-size="11" font-weight="700"
    letter-spacing="1.3" fill="#67E8F9">
    KEY TAKEAWAY
  </text>

  {svg_lines(takeaway_lines, margin+22, panel_y+51, 16, "#F8FAFC", 600, 24)}

  <line x1="{margin}" y1="{footer_line_y}"
    x2="{width-margin}" y2="{footer_line_y}"
    stroke="#475569" opacity=".7"/>

  <text x="{margin}" y="{footer_y}"
    font-size="13" font-weight="600" fill="#E2E8F0">
    CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="{width-margin}" y="{footer_y}"
    font-size="13" text-anchor="end" fill="#67E8F9">
    POWERED BY GOOGLE GEMINI
  </text>

</g>
</svg>
'''


# =====================================================
# README
# =====================================================

def update_readme(content):
    start = content.index(START_MARKER) + len(START_MARKER)
    end = content.index(END_MARKER)

    section = (
        "\n\n"
        "![Daily AI Tech Insight by Muhammad Junaeid]"
        "(./assets/ai-tech-insight.svg)"
        "\n\n"
    )

    updated = content[:start] + section + content[end:]

    # Normalize all line endings and trailing whitespace.
    updated = updated.replace("\r\n", "\n").replace("\r", "\n")
    updated = "\n".join(
        line.rstrip(" \t")
        for line in updated.splitlines()
    ).rstrip() + "\n"

    return updated


# =====================================================
# MAIN
# =====================================================

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY secret is missing.")

    log("Checking repository files...")

    date = get_today()
    readme_content = read_readme()
    previous = read_history()

    # Reuse today's valid insight if it already exists.
    if previous.get("date") == date:
        log("Today's insight already exists. Checking generated assets.")

        insight = validate_insight({
            key: previous[key]
            for key in REQUIRED_FIELDS
            if key in previous
        })

    else:
        log("Generating a new daily insight...")

        insight = generate_insight(api_key, previous)

    # Always regenerate the presentation files.
    # This repairs missing or outdated files even on the same date.
    log("Generating SVG...")

    svg_content = create_svg(insight, date)

    # Verify SVG before writing.
    try:
        svg_root = ET.fromstring(svg_content)
    except ET.ParseError:
        raise RuntimeError("Generated SVG is invalid XML.") from None

    if not svg_root.tag.endswith("svg"):
        raise RuntimeError("Generated SVG has an invalid root.")

    if svg_root.attrib.get("viewBox") != "0 0 1200 630":
        raise RuntimeError("Generated SVG dimensions are invalid.")

    log("Updating README...")

    updated_readme = update_readme(readme_content)

    # Preserve original metadata when reusing today's insight.
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

    # Validate JSON before writing.
    json.loads(json_content)

    # Write all outputs.
    log("Writing generated files...")

    atomic_write(DATA_PATH, json_content)
    atomic_write(SVG_PATH, svg_content)
    atomic_write(README_PATH, updated_readme)

    # Verify files after writing.
    if not DATA_PATH.is_file() or not DATA_PATH.stat().st_size:
        raise RuntimeError("JSON output was not created.")

    if not SVG_PATH.is_file() or not SVG_PATH.stat().st_size:
        raise RuntimeError("SVG output was not created.")

    if not README_PATH.is_file() or not README_PATH.stat().st_size:
        raise RuntimeError("README output was not created.")

    log("======================================")
    log("AI TECH INSIGHT GENERATED SUCCESSFULLY")
    log(f"Date: {date}")
    log(f"Title: {insight['title']}")
    log(f"Category: {insight['category']}")
    log(f"Author: {OWNER}")
    log("SVG: assets/ai-tech-insight.svg")
    log("======================================")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"Workflow failed safely: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
