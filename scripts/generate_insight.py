import time
import random
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from google.genai import types

from google import genai


# -----------------------------
# Configuration
# -----------------------------

ROOT = Path(__file__).resolve().parents[1]

README_PATH = ROOT / "README.md"
DATA_DIR = ROOT / "data"
DATA_PATH = DATA_DIR / "insight.json"

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

MODEL = "gemini-3.8-flash"
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
    client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(
        timeout=90000
    )
)

    previous_title = previous.get("title", "None")
    previous_category = previous.get("category", "None")

    prompt = f"""
You are a careful technology educator writing a daily
educational insight for a software developer's GitHub profile.

Date: {today}

Previous insight title: {previous_title}
Previous category: {previous_category}

Create ONE useful, evergreen technology insight.

Choose exactly one category from this list:
{", ".join(sorted(ALLOWED_CATEGORIES))}

Requirements:
1. Explain one meaningful technical concept.
2. Make it useful to developers and technology learners.
3. Use accurate, clear, beginner-friendly English.
4. Avoid repeating the previous insight.
5. Do not invent statistics, studies, quotations, or sources.
6. Do not present speculation as established fact.
7. Do not include breaking news.
8. Do not include URLs, HTML, code, or Markdown formatting.
9. Return only valid JSON.

Required JSON structure:
{{
  "title": "A short insight title",
  "category": "One approved category",
  "explanation": "A clear explanation of the concept.",
  "takeaway": "One practical lesson for developers."
}}
"""

    max_attempts = 4

    for attempt in range(max_attempts):
        try:
            response = client.interactions.create(
                model=MODEL,
                input=prompt,
            )

            output = response.output_text

            if not isinstance(output, str) or not output.strip():
                raise RuntimeError("Gemini returned an empty response.")

            try:
                parsed = json.loads(output.strip())
            except json.JSONDecodeError:
                raise RuntimeError(
                    "Gemini returned invalid JSON."
                ) from None

            return validate_response(parsed)

        except Exception as exc:
            status = getattr(exc, "code", None)

            if status is None:
                response_obj = getattr(exc, "response", None)
                status = getattr(response_obj, "status_code", None)

            error_name = type(exc).__name__

            # Retry only temporary server/network failures.
            retryable = (
                status in (408, 429, 500, 502, 503, 504)
                or error_name in (
                    "InternalServerError",
                    "ServiceUnavailable",
                    "DeadlineExceeded",
                    "TimeoutException",
                    "ConnectError",
                )
            )

            if not retryable:
                if isinstance(exc, RuntimeError):
                    raise

                raise RuntimeError(
                    f"Gemini request failed. Error type: {error_name}"
                ) from None

            if attempt == max_attempts - 1:
                raise RuntimeError(
                    f"Gemini service remained unavailable after "
                    f"{max_attempts} attempts. Last error: {error_name}"
                ) from None

            delay = min(2 ** (attempt + 1), 16) + random.uniform(0, 1)

            print(
                f"Temporary Gemini API failure "
                f"(attempt {attempt + 1}/{max_attempts}). "
                f"Retrying in {delay:.1f} seconds."
            )

            time.sleep(delay)

    raise RuntimeError("Gemini request failed.")


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
