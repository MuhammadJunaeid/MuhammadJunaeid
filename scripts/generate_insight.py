
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


# --------------------------------------------------
# Configuration
# --------------------------------------------------

MODEL = "gemini-3.5-flash-lite"

API_TIMEOUT_SECONDS = 45
MAX_ATTEMPTS = 3

ROOT_DIR = Path(__file__).resolve().parent.parent
README_PATH = ROOT_DIR / "README.md"
DATA_PATH = ROOT_DIR / "data" / "insight.json"

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

ALLOWED_CATEGORIES = {
    "Artificial Intelligence",
    "Web Development",
    "Cybersecurity",
    "Software Engineering",
    "Emerging Technology",
}

REQUIRED_FIELDS = {
    "title",
    "category",
    "explanation",
    "takeaway",
}

RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


# --------------------------------------------------
# Date and file utilities
# --------------------------------------------------

def get_today():
    """Return today's date in Bangladesh time."""
    return datetime.now(
        ZoneInfo("Asia/Dhaka")
    ).date().isoformat()


def atomic_write(path, content):
    """
    Safely write a file using a temporary file.
    This prevents partially written files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)

        os.replace(temp_path, path)

    except Exception:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def read_readme():
    if not README_PATH.is_file():
        raise RuntimeError("README.md was not found.")

    content = README_PATH.read_text(encoding="utf-8")

    if content.count(START_MARKER) != 1:
        raise RuntimeError(
            "README must contain exactly one INSIGHT:START marker."
        )

    if content.count(END_MARKER) != 1:
        raise RuntimeError(
            "README must contain exactly one INSIGHT:END marker."
        )

    if content.index(START_MARKER) >= content.index(END_MARKER):
        raise RuntimeError(
            "README insight markers are in the wrong order."
        )

    return content


# --------------------------------------------------
# Input validation and Markdown safety
# --------------------------------------------------

def clean_text(value, field_name, max_length):
    if not isinstance(value, str):
        raise ValueError(
            f"Invalid response: {field_name} must be text."
        )

    value = " ".join(value.split())

    if not value:
        raise ValueError(
            f"Invalid response: {field_name} is empty."
        )

    if len(value) > max_length:
        raise ValueError(
            f"Invalid response: {field_name} is too long."
        )

    if re.search(r"<[^>]*>", value):
        raise ValueError(
            f"Invalid response: HTML is not allowed in {field_name}."
        )

    if re.search(
        r"(https?://|www\.)",
        value,
        flags=re.IGNORECASE,
    ):
        raise ValueError(
            f"Invalid response: URLs are not allowed in {field_name}."
        )

    if any(ord(char) < 32 for char in value):
        raise ValueError(
            f"Invalid response: control characters in {field_name}."
        )

    return value


def validate_response(data):
    if not isinstance(data, dict):
        raise ValueError("Gemini returned an invalid JSON object.")

    if set(data.keys()) != REQUIRED_FIELDS:
        raise ValueError(
            "Gemini response does not match the required schema."
        )

    title = clean_text(data["title"], "title", 100)
    category = clean_text(data["category"], "category", 40)
    explanation = clean_text(
        data["explanation"],
        "explanation",
        600,
    )
    takeaway = clean_text(
        data["takeaway"],
        "takeaway",
        240,
    )

    if category not in ALLOWED_CATEGORIES:
        raise ValueError(
            "Gemini returned an unsupported category."
        )

    if len(explanation) < 40:
        raise ValueError(
            "Gemini explanation is too short."
        )

    if len(takeaway) < 15:
        raise ValueError(
            "Gemini takeaway is too short."
        )

    return {
        "title": title,
        "category": category,
        "explanation": explanation,
        "takeaway": takeaway,
    }


def escape_markdown(value):
    """
    Escape Markdown syntax so generated text cannot
    unexpectedly create links, headings, or formatting.
    """
    return re.sub(
        r"([\\`*_{}\[\]()#+.!|>~-])",
        r"\\\1",
        value,
    )


# --------------------------------------------------
# Gemini API
# --------------------------------------------------

def build_prompt(today):
    categories = ", ".join(sorted(ALLOWED_CATEGORIES))

    return f"""
Create one useful, accurate, beginner-friendly technology insight
for a developer's GitHub profile.

Date: {today}

Choose exactly one category from this list:
{categories}

Requirements:
- Focus on one specific technical concept or practical lesson.
- Explain why it matters in real development or technology work.
- Use clear, natural English.
- Avoid vague motivational statements.
- Avoid breaking news, current events, and unsupported statistics.
- Do not invent research, sources, quotes, or facts.
- Do not include URLs, HTML, Markdown, code blocks, or emojis.
- Do not mention that you are an AI.
- Keep the explanation useful to a beginner but technically correct.

Return only a valid JSON object with exactly these four string fields:
{{
  "title": "A short and useful title",
  "category": "One category from the allowed list",
  "explanation": "A clear explanation in approximately 2 to 4 sentences",
  "takeaway": "One practical lesson in 1 sentence"
}}
""".strip()


def extract_response_text(api_result):
    candidates = api_result.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            "Gemini returned no response candidates."
        )

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])

    text_parts = [
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
    ]

    if not text_parts:
        raise RuntimeError(
            "Gemini returned no usable text."
        )

    return "".join(text_parts).strip()


def request_gemini(api_key, prompt):
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
        try:
            with urllib.request.urlopen(
                request,
                timeout=API_TIMEOUT_SECONDS,
            ) as response:
                response_body = response.read()

            api_result = json.loads(
                response_body.decode("utf-8")
            )

            generated_text = extract_response_text(api_result)
            parsed_data = json.loads(generated_text)

            return validate_response(parsed_data)

        except urllib.error.HTTPError as error:
            status_code = error.code

            if (
                status_code not in RETRYABLE_STATUS_CODES
                or attempt == MAX_ATTEMPTS
            ):
                raise RuntimeError(
                    f"Gemini API request failed with HTTP {status_code}."
                ) from None

            print(
                f"Gemini temporarily unavailable "
                f"(HTTP {status_code}). Retry {attempt}/{MAX_ATTEMPTS}."
            )

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ):
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    "Gemini request failed after multiple network attempts."
                ) from None

            print(
                f"Network timeout or connection error. "
                f"Retry {attempt}/{MAX_ATTEMPTS}."
            )

        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError(
                "Gemini returned invalid JSON."
            ) from None

        if attempt < MAX_ATTEMPTS:
            delay = min(
                (2 ** attempt) + random.uniform(0, 1),
                8,
            )
            time.sleep(delay)

    raise RuntimeError("Gemini request failed.")


# --------------------------------------------------
# README rendering
# --------------------------------------------------

def render_insight(insight, today):
    title = escape_markdown(insight["title"])
    category = escape_markdown(insight["category"])
    explanation = escape_markdown(insight["explanation"])
    takeaway = escape_markdown(insight["takeaway"])

    section = f"""### {title}

**Category:** {category}

**Updated:** {today}

{explanation}

**Key takeaway:** {takeaway}

<sub>AI-generated educational content. Verify important technical claims before relying on them.</sub>"""

    # Reject trailing spaces or tabs in generated content.
    for line in section.splitlines():
        if line.endswith((" ", "\t")):
            raise RuntimeError(
                "Generated README section contains trailing whitespace."
            )

    return section


def update_readme(readme_content, section):
    start_position = (
        readme_content.index(START_MARKER)
        + len(START_MARKER)
    )

    end_position = readme_content.index(END_MARKER)

    updated_content = (
        readme_content[:start_position]
        + "\n\n"
        + section
        + "\n\n"
        + readme_content[end_position:]
    )

    return updated_content


# --------------------------------------------------
# Main workflow
# --------------------------------------------------

def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY secret is missing."
        )

    today = get_today()

    # Validate README before making an API request.
    readme_content = read_readme()

    print("Requesting today's technology insight...")

    insight = request_gemini(
        api_key,
        build_prompt(today),
    )

    # Prepare both output files before writing either one.
    section = render_insight(insight, today)

    updated_readme = update_readme(
        readme_content,
        section,
    )

    if START_MARKER not in updated_readme:
        raise RuntimeError("README marker validation failed.")

    if END_MARKER not in updated_readme:
        raise RuntimeError("README marker validation failed.")

    insight_record = {
        **insight,
        "date": today,
    }

    json_content = json.dumps(
        insight_record,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    # Write the generated files.
    atomic_write(DATA_PATH, json_content)
    atomic_write(README_PATH, updated_readme)

    print("Today's AI Tech Insight was generated successfully.")
    print(f"Category: {insight['category']}")
    print(f"Date: {today}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Only print controlled error messages.
        # Never print API keys or raw API response bodies.
        print(
            f"Workflow failed safely: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
