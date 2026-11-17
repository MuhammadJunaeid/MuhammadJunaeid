import time
import random
import urllib.request
import urllib.error
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo



# -----------------------------
# Configuration
# -----------------------------

ROOT = Path(__file__).resolve().parents[1]

README_PATH = ROOT / "README.md"
DATA_DIR = ROOT / "data"
DATA_PATH = DATA_DIR / "insight.json"

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

MODEL = "gemini-3.5-flash-lite"
TIMEZONE = ZoneInfo("Asia/Dhaka")

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

MAX_TITLE = 100
MAX_EXPLANATION = 500
MAX_TAKEAWAY = 200


# -----------------------------
# Validation
# -----------------------------

def validate_text(value, field_name, max_length):
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field_name} type.")

    value = " ".join(value.split()).strip()

    if not value:
        raise ValueError(f"Empty {field_name}.")

    if len(value) > max_length:
        raise ValueError(f"{field_name} exceeds length limit.")

    # Do not allow HTML, URLs, or control characters.
    if "<" in value or ">" in value:
        raise ValueError(f"HTML is not allowed in {field_name}.")

    if re.search(r"https?://|www\.", value, re.IGNORECASE):
        raise ValueError(f"URLs are not allowed in {field_name}.")

    if any(ord(char) < 32 for char in value):
        raise ValueError(f"Control characters in {field_name}.")

    return value


def escape_markdown(value):
    # Prevent generated text from creating unexpected
    # Markdown links, headings, formatting, or tables.
    return re.sub(
        r"([\\`*_{}\[\]()#+\-.!|>])",
        r"\\\1",
        value,
    )


def validate_response(data):
    if not isinstance(data, dict):
        raise ValueError("AI response must be a JSON object.")

    required = {
        "title",
        "category",
        "explanation",
        "takeaway",
    }

    if set(data.keys()) != required:
        raise ValueError("Unexpected response structure.")

    category = data["category"]

    if category not in ALLOWED_CATEGORIES:
        raise ValueError("Unapproved insight category.")

    return {
        "title": validate_text(
            data["title"], "title", MAX_TITLE
        ),
        "category": category,
        "explanation": validate_text(
            data["explanation"],
            "explanation",
            MAX_EXPLANATION,
        ),
        "takeaway": validate_text(
            data["takeaway"],
            "takeaway",
            MAX_TAKEAWAY,
        ),
    }


# -----------------------------
# Read previous insight
# -----------------------------

def read_previous_insight():
    if not DATA_PATH.exists():
        return {}

    try:
        with DATA_PATH.open("r", encoding="utf-8") as file:
            previous = json.load(file)

        if not isinstance(previous, dict):
            raise ValueError("Invalid history file.")

        return previous

    except (OSError, json.JSONDecodeError, ValueError):
        raise RuntimeError(
            "Previous insight data is invalid. "
            "Check data/insight.json."
        ) from None


# -----------------------------
# Generate insight
# -----------------------------


def generate_insight(api_key, today, previous):

    previous_title = previous.get("title", "None")
    previous_category = previous.get("category", "None")

    prompt = f"""
You are a careful technology educator.

Create ONE useful, evergreen technology insight
for a software developer's GitHub profile.

Date: {today}

Previous title: {previous_title}
Previous category: {previous_category}

Choose exactly one category:
{", ".join(sorted(ALLOWED_CATEGORIES))}

Requirements:
1. Explain one meaningful technical concept.
2. Use accurate, beginner-friendly English.
3. Avoid repeating the previous insight.
4. Do not invent statistics or sources.
5. Do not include breaking news.
6. Do not include URLs, HTML, or Markdown.
7. Keep the explanation concise.
8. Return only valid JSON.

Required structure:
{{
  "title": "A short title",
  "category": "One approved category",
  "explanation": "A clear explanation.",
  "takeaway": "One practical lesson."
}}
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{MODEL}:generateContent"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 512
        }
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key
        },
        method="POST"
    )

    max_attempts = 3

    for attempt in range(max_attempts):

        print(
            f"Contacting Gemini "
            f"(attempt {attempt + 1}/{max_attempts})...",
            flush=True
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=45
            ) as response:

                result = json.loads(
                    response.read().decode("utf-8")
                )

            candidates = result.get("candidates", [])

            if not candidates:
                raise RuntimeError(
                    "Gemini returned no candidates."
                )

            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )

            output = "".join(
                part.get("text", "")
                for part in parts
            ).strip()

            if not output:
                raise RuntimeError(
                    "Gemini returned empty text."
                )

            parsed = json.loads(output)

            validated = validate_response(parsed)

            print("Gemini response validated.", flush=True)

            return validated

        except urllib.error.HTTPError as exc:

            status = exc.code

            # Do not print the response body or credentials.
            if status not in (408, 429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"Gemini returned HTTP {status}. "
                    "Check API access and request configuration."
                ) from None

            if attempt == max_attempts - 1:
                raise RuntimeError(
                    f"Gemini remained unavailable. "
                    f"HTTP status: {status}"
                ) from None

            delay = (2 ** (attempt + 1)) + random.uniform(0, 1)

            print(
                f"Temporary HTTP {status}. "
                f"Retrying in {delay:.1f} seconds.",
                flush=True
            )

            time.sleep(delay)

        except (TimeoutError, urllib.error.URLError) as exc:

            if attempt == max_attempts - 1:
                raise RuntimeError(
                    "Gemini request timed out or network failed "
                    "after three attempts."
                ) from None

            delay = (2 ** (attempt + 1)) + random.uniform(0, 1)

            print(
                f"Network timeout or connection failure. "
                f"Retrying in {delay:.1f} seconds.",
                flush=True
            )

            time.sleep(delay)

        except (json.JSONDecodeError, ValueError):
            raise RuntimeError(
                "Gemini returned invalid JSON or content."
            ) from None

    raise RuntimeError("Gemini generation failed.")


# -----------------------------
# Render README section
# -----------------------------

def render_insight(insight, today):
    title = escape_markdown(insight["title"])
    category = escape_markdown(insight["category"])
    explanation = escape_markdown(insight["explanation"])
    takeaway = escape_markdown(insight["takeaway"])

    return f"""### {title}

**Category:** {category}  
**Updated:** {today}

{explanation}

**Key takeaway:** {takeaway}

<sub>AI-generated educational content. Verify important technical claims before relying on them.</sub>"""


# -----------------------------
# Safe README update
# -----------------------------

def update_readme(section):
    if not README_PATH.is_file():
        raise RuntimeError("README.md was not found.")

    content = README_PATH.read_text(encoding="utf-8")

    # Require exactly one occurrence of each marker.
    if content.count(START_MARKER) != 1:
        raise RuntimeError("Invalid start marker configuration.")

    if content.count(END_MARKER) != 1:
        raise RuntimeError("Invalid end marker configuration.")

    before, remainder = content.split(START_MARKER, 1)

    if END_MARKER not in remainder:
        raise RuntimeError("README markers are incorrectly ordered.")

    _, after = remainder.split(END_MARKER, 1)

    updated = (
        before
        + START_MARKER
        + "\n"
        + section
        + "\n"
        + END_MARKER
        + after
    )

    if updated == content:
        print("README is already up to date.")
        return False

    return updated


# -----------------------------
# Atomic file write
# -----------------------------

def atomic_write(path, content):
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    try:
        temporary_path.write_text(
            content,
            encoding="utf-8",
        )
        os.replace(temporary_path, path)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


# -----------------------------
# Main
# -----------------------------

def main():
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    today = datetime.now(TIMEZONE).date().isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    previous = read_previous_insight()

    insight = generate_insight(
        api_key,
        today,
        previous,
    )

    section = render_insight(insight, today)
    updated_readme = update_readme(section)

    if updated_readme is False:
        return

    new_history = {
        **insight,
        "date": today,
    }

    # Prepare both outputs before replacing either file.
    history_text = json.dumps(
        new_history,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    atomic_write(DATA_PATH, history_text)
    atomic_write(README_PATH, updated_readme)

    print("Daily technology insight generated and validated.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Workflow failed safely: {exc}")
        sys.exit(1)
