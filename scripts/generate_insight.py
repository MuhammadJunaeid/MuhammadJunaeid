from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
import textwrap

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google import genai


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

README_FILE = ROOT / "README.md"
JSON_FILE = ROOT / "data" / "insight.json"
SVG_FILE = ROOT / "assets" / "ai-tech-insight.svg"

TIMEZONE = ZoneInfo("Asia/Dhaka")

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

MAX_RETRIES = 3

MAX_TITLE_LENGTH = 100
MAX_CATEGORY_LENGTH = 60
MAX_EXPLANATION_LENGTH = 1200
MAX_TAKEAWAY_LENGTH = 400

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("automated-tech-insight")


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are an expert technology researcher and technical educator.

Your task is to create one original, high-quality daily technology
insight for a professional developer technology journal.

The journal covers:

- Software engineering
- Cybersecurity
- Cloud computing
- Artificial intelligence
- Networking
- Programming languages
- Developer tools
- Databases
- Operating systems
- Distributed systems
- Emerging technologies
- System architecture
- DevOps
- Web technologies

CONTENT REQUIREMENTS:

1. Select one genuinely interesting technical concept.
2. Avoid generic motivational content.
3. Explain the underlying technical mechanism accurately.
4. Use precise technical terminology.
5. Make the explanation understandable to developers.
6. Avoid unsupported performance claims.
7. Do not invent security guarantees.
8. Keep the explanation between 55 and 100 words.
9. Write one practical takeaway between 15 and 35 words.
10. Do not repeat a previously supplied insight if the
    previous insight is included in the input.
11. Choose the most appropriate category.
12. Return valid JSON only.

OUTPUT STRUCTURE:

{
    "title": "Technical concept",
    "category": "Appropriate category",
    "explanation": "Technical explanation.",
    "takeaway": "Practical takeaway."
}

Do not include Markdown fences.
Do not include introductory text.
Do not include additional JSON properties.
"""


# ============================================================
# FILE SYSTEM
# ============================================================

def ensure_directories() -> None:
    """Create output directories when necessary."""

    JSON_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    SVG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


def load_previous_insight() -> dict[str, Any] | None:
    """Load the previously published insight."""

    if not JSON_FILE.exists():
        return None

    try:
        with JSON_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

    except (OSError, json.JSONDecodeError) as error:
        logger.warning(
            "Could not read previous insight: %s",
            error,
        )

    return None


# ============================================================
# GEMINI GENERATION
# ============================================================

def build_prompt(
    previous_insight: dict[str, Any] | None,
) -> str:
    """Build the generation prompt."""

    prompt = """
Generate one technically accurate and distinctive
technology insight for today's publication.

Prioritize concepts that developers can apply or use
to improve their technical understanding.
"""

    if previous_insight:
        previous_title = previous_insight.get(
            "title",
            "Unknown",
        )

        previous_category = previous_insight.get(
            "category",
            "Unknown",
        )

        prompt += f"""

PREVIOUSLY PUBLISHED INSIGHT:

Title: {previous_title}
Category: {previous_category}

Choose a different technical concept.
Avoid simply renaming the previous topic.
"""

    return prompt


def extract_json(response_text: str) -> dict[str, Any]:
    """Extract a JSON object from the model response."""

    if not response_text or not response_text.strip():
        raise ValueError("Gemini returned an empty response.")

    cleaned = response_text.strip()

    # Remove Markdown code fences if present.
    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned,
    )

    try:
        result = json.loads(cleaned)

    except json.JSONDecodeError:
        # Attempt to recover the first complete JSON object.
        match = re.search(
            r"\{.*\}",
            cleaned,
            flags=re.DOTALL,
        )

        if not match:
            raise ValueError(
                "Gemini did not return valid JSON."
            )

        result = json.loads(match.group(0))

    if not isinstance(result, dict):
        raise ValueError(
            "Gemini response must be a JSON object."
        )

    return result


def validate_insight(
    insight: dict[str, Any],
) -> dict[str, str]:
    """Validate and normalize generated content."""

    required_fields = (
        "title",
        "category",
        "explanation",
        "takeaway",
    )

    for field in required_fields:
        value = insight.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Missing or invalid field: {field}"
            )

    result = {
        field: insight[field].strip()
        for field in required_fields
    }

    limits = {
        "title": MAX_TITLE_LENGTH,
        "category": MAX_CATEGORY_LENGTH,
        "explanation": MAX_EXPLANATION_LENGTH,
        "takeaway": MAX_TAKEAWAY_LENGTH,
    }

    for field, maximum in limits.items():
        if len(result[field]) > maximum:
            raise ValueError(
                f"{field} exceeds {maximum} characters."
            )

    if len(result["explanation"].split()) < 30:
        raise ValueError(
            "Explanation is too short."
        )

    if len(result["takeaway"].split()) < 8:
        raise ValueError(
            "Takeaway is too short."
        )

    return result


def generate_insight(
    api_key: str,
    previous_insight: dict[str, Any] | None,
) -> dict[str, str]:
    """
    Generate an insight using the modern Interactions API.

    IMPORTANT:
    google-genai 2.x uses the new Interactions response schema.

    Read generated text through response.output_text.
    Do not access the removed legacy response.outputs schema.
    """

    client = genai.Client(
        api_key=api_key,
    )

    prompt = build_prompt(previous_insight)

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            logger.info(
                "Generating insight using %s (attempt %s/%s)",
                MODEL,
                attempt,
                MAX_RETRIES,
            )

            response = client.interactions.create(
                model=MODEL,
                input=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT + "\n\n" + prompt,
                    }
                ],
                store=False,
            )

            # New SDK response interface.
            # Do not use response.outputs.
            response_text = response.output_text

            if not response_text:
                raise ValueError(
                    "Gemini returned no output text."
                )

            generated = extract_json(response_text)

            validated = validate_insight(generated)

            logger.info(
                "Successfully generated: %s",
                validated["title"],
            )

            return validated

        except Exception as error:
            last_error = error

            logger.exception(
                "Generation attempt %s failed.",
                attempt,
            )

            if attempt < MAX_RETRIES:
                import time

                delay = 2 ** attempt

                logger.info(
                    "Retrying in %s seconds...",
                    delay,
                )

                time.sleep(delay)

    raise RuntimeError(
        f"Gemini generation failed after "
        f"{MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# DATE AND METADATA
# ============================================================

def create_metadata(
    insight: dict[str, str],
) -> dict[str, str]:
    """Attach publication metadata."""

    now = datetime.now(TIMEZONE)

    return {
        **insight,
        "date": now.isoformat(timespec="seconds"),
    }


# ============================================================
# JSON OUTPUT
# ============================================================

def save_json(
    insight: dict[str, str],
) -> None:
    """Save the structured insight."""

    with JSON_FILE.open("w", encoding="utf-8") as file:

        json.dump(
            insight,
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")

    logger.info(
        "JSON saved: %s",
        JSON_FILE.relative_to(ROOT),
    )


# ============================================================
# SVG HELPERS
# ============================================================

def svg_escape(value: str) -> str:
    """Safely escape text inserted into SVG."""

    return html.escape(
        value,
        quote=True,
    )


def wrap_text(
    value: str,
    width: int,
) -> list[str]:
    """Wrap text into SVG-friendly lines."""

    return textwrap.wrap(
        value,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )


def svg_text_lines(
    lines: list[str],
    x: int,
    y: int,
    font_size: int,
    fill: str,
    weight: int = 400,
    line_height: int = 58,
) -> str:
    """Create multiline SVG text."""

    output = [
        (
            f'<text x="{x}" y="{y}" '
            f'font-size="{font_size}" '
            f'font-weight="{weight}" '
            f'fill="{fill}">'
        )
    ]

    for index, line in enumerate(lines):

        output.append(
            f'<tspan x="{x}" '
            f'dy="{0 if index == 0 else line_height}">'
            f'{svg_escape(line)}</tspan>'
        )

    output.append("</text>")

    return "\n".join(output)


# ============================================================
# SVG GENERATION
# ============================================================

def create_svg(
    insight: dict[str, str],
) -> str:
    """Generate the branded technology journal artwork."""

    title = svg_escape(insight["title"])
    category = svg_escape(insight["category"])

    explanation_lines = wrap_text(
        insight["explanation"],
        76,
    )

    takeaway_lines = wrap_text(
        insight["takeaway"],
        72,
    )

    date = datetime.fromisoformat(
        insight["date"]
    )

    date_label = date.strftime(
        "%Y-%m-%d %H:%M:%S %z"
    )

    explanation_svg = svg_text_lines(
        explanation_lines,
        220,
        635,
        39,
        "#D8E3F5",
        400,
        62,
    )

    takeaway_svg = svg_text_lines(
        takeaway_lines,
        265,
        1040,
        37,
        "#FFFFFF",
        650,
        58,
    )

    # Unique version identifier for cache invalidation.
    version = hashlib.sha256(
        json.dumps(
            insight,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="3840"
     height="2160"
     viewBox="0 0 3840 2160"
     role="img"
     aria-labelledby="svg-title svg-desc">

<title id="svg-title">{title}</title>

<desc id="svg-desc">
{svg_escape(insight["explanation"])}
</desc>

<defs>

    <linearGradient id="bg"
        x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="#080C18"/>
        <stop offset="50%" stop-color="#111B31"/>
        <stop offset="100%" stop-color="#17102C"/>
    </linearGradient>

    <linearGradient id="metal"
        x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="#F472D0"/>
        <stop offset="48%" stop-color="#F9C46B"/>
        <stop offset="100%" stop-color="#72E6A0"/>
    </linearGradient>

    <radialGradient id="glow">
        <stop offset="0%" stop-color="#D946EF"
            stop-opacity=".48"/>
        <stop offset="55%" stop-color="#A855F7"
            stop-opacity=".15"/>
        <stop offset="100%" stop-color="#080C18"
            stop-opacity="0"/>
    </radialGradient>

    <pattern id="grid"
        width="60" height="60"
        patternUnits="userSpaceOnUse">
        <circle cx="2" cy="2" r="2"
            fill="#A5B4FC" opacity=".15"/>
    </pattern>

    <clipPath id="clip">
        <rect width="3840" height="2160" rx="48"/>
    </clipPath>

</defs>

<style>

    .wave {{
        fill: none;
        stroke-dasharray: 18 28;
        animation: flow 8s linear infinite;
    }}

    .pulse {{
        transform-box: fill-box;
        transform-origin: center;
        animation: pulse 5s ease-in-out infinite alternate;
    }}

    @keyframes flow {{
        to {{ stroke-dashoffset: -184; }}
    }}

    @keyframes pulse {{
        from {{ opacity: .55; }}
        to {{ opacity: 1; }}
    }}

    @media (prefers-reduced-motion: reduce) {{
        .wave, .pulse {{
            animation: none;
        }}
    }}

</style>

<g clip-path="url(#clip)">

    <!-- BACKGROUND -->

    <rect width="3840" height="2160"
        fill="url(#bg)"/>

    <rect width="3840" height="2160"
        fill="url(#grid)"/>

    <circle cx="3050" cy="900" r="1100"
        fill="url(#glow)" class="pulse"/>

    <circle cx="3450" cy="300" r="850"
        fill="url(#glow)"/>

    <!-- ATMOSPHERIC LINES -->

    <path d="M2300 0 L3840 1100"
        stroke="url(#metal)"
        stroke-width="3"
        opacity=".12"/>

    <path d="M2450 2160 L3840 1000"
        stroke="url(#metal)"
        stroke-width="3"
        opacity=".12"/>

    <!-- BRAND -->

    <g font-family="Inter, Segoe UI, Arial, sans-serif">

        <text x="220" y="200"
            font-size="54"
            font-weight="800"
            letter-spacing="5"
            fill="#FFFFFF">
            JUNAEID
            <tspan fill="#F472D0"> / </tspan>
            TECH JOURNAL
        </text>

        <rect x="220" y="275"
            width="9" height="47"
            rx="4"
            fill="url(#metal)"/>

        <text x="254" y="310"
            font-size="34"
            font-weight="750"
            letter-spacing="4"
            fill="#F472D0">
            {category.upper()}
        </text>

        <!-- TITLE -->

        <text x="220" y="480"
            font-size="90"
            font-weight="750"
            fill="#FFFFFF">
            {title}
        </text>

        <line x1="220" y1="550"
            x2="2150" y2="550"
            stroke="#94A3B8"
            opacity=".28"
            stroke-width="3"/>

        <!-- EXPLANATION -->

        {explanation_svg}

        <!-- TAKEAWAY -->

        <rect x="220" y="910"
            width="1950"
            height="280"
            rx="32"
            fill="#FFFFFF"
            fill-opacity=".055"
            stroke="#FFFFFF"
            stroke-opacity=".12"
            stroke-width="2"/>

        <rect x="220" y="910"
            width="9"
            height="280"
            rx="4"
            fill="url(#metal)"/>

        <text x="265" y="985"
            font-size="31"
            font-weight="800"
            letter-spacing="4"
            fill="#F472D0">
            KEY TAKEAWAY
        </text>

        {takeaway_svg}

        <!-- FOOTER -->

        <line x1="220" y1="2010"
            x2="3610" y2="2010"
            stroke="#94A3B8"
            opacity=".25"
            stroke-width="2"/>

        <text x="220" y="2075"
            font-size="29"
            font-weight="750"
            letter-spacing="2"
            fill="#E2E8F0">
            CREATED BY MUHAMMAD JUNAEID
        </text>

        <text x="3610" y="2075"
            text-anchor="end"
            font-size="25"
            fill="#A5B4FC">
            {date_label} · DHAKA
        </text>

    </g>

    <!-- ABSTRACT DATA VISUALIZATION -->

    <g transform="translate(2300 330)">

        <circle cx="600" cy="650" r="650"
            fill="url(#glow)"/>

        <circle cx="600" cy="650" r="570"
            fill="none"
            stroke="url(#metal)"
            stroke-width="3"
            opacity=".22"
            stroke-dasharray="12 22"
            class="pulse"/>

        <path
            d="M25 650
               C160 510 270 780 400 650
               S650 510 780 650
               S1040 790 1175 650"
            stroke="#F472D0"
            stroke-width="7"
            opacity=".8"
            class="wave"/>

        <path
            d="M25 750
               C160 600 270 900 400 750
               S650 600 780 750
               S1040 900 1175 750"
            stroke="#F9C46B"
            stroke-width="5"
            opacity=".8"
            class="wave"/>

        <path
            d="M25 850
               C160 700 270 1000 400 850
               S650 700 780 850
               S1040 1000 1175 850"
            stroke="#72E6A0"
            stroke-width="5"
            opacity=".8"
            class="wave"/>

        <circle cx="400" cy="650" r="9"
            fill="#F472D0"/>

        <circle cx="780" cy="650" r="9"
            fill="#F9C46B"/>

        <circle cx="1175" cy="650" r="9"
            fill="#72E6A0"/>

    </g>

    <!-- PUBLICATION IDENTITY -->

    <text x="3610" y="2110"
        text-anchor="end"
        font-family="Inter, Segoe UI, Arial, sans-serif"
        font-size="22"
        font-weight="650"
        letter-spacing="4"
        fill="#F472D0">
        DIGITAL INTELLIGENCE JOURNAL
    </text>

</g>
</svg>
"""

    return svg


def save_svg(
    insight: dict[str, str],
) -> None:
    """Generate and save SVG artwork."""

    svg = create_svg(insight)

    with SVG_FILE.open("w", encoding="utf-8") as file:
        file.write(svg)

    logger.info(
        "SVG saved: %s",
        SVG_FILE.relative_to(ROOT),
    )


# ============================================================
# README GENERATION
# ============================================================

def create_readme_section(
    insight: dict[str, str],
) -> str:
    """Build the daily README publication section."""

    version = hashlib.sha256(
        json.dumps(
            insight,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    date = datetime.fromisoformat(
        insight["date"]
    )

    timestamp = date.isoformat(
        timespec="seconds"
    )

    return f"""<div align="center">

### AUTOMATED TECH INSIGHT OF THE DAY

**{insight["title"]}**

`{insight["category"]}` · `{timestamp}`

![Junaeid Tech Journal](assets/ai-tech-insight.svg?v={version})

</div>

**The insight**

{insight["explanation"]}

> **KEY TAKEAWAY**
>
> {insight["takeaway"]}

<sub>Created by Muhammad Junaeid · AI-assisted technology journal</sub>"""


def update_readme(
    insight: dict[str, str],
) -> None:
    """Replace only the marked README section."""

    if not README_FILE.exists():
        raise FileNotFoundError(
            "README.md does not exist."
        )

    content = README_FILE.read_text(
        encoding="utf-8"
    )

    if START_MARKER not in content:
        raise ValueError(
            "README is missing INSIGHT:START marker."
        )

    if END_MARKER not in content:
        raise ValueError(
            "README is missing INSIGHT:END marker."
        )

    start_index = content.index(START_MARKER)
    end_index = content.index(END_MARKER)

    if end_index < start_index:
        raise ValueError(
            "README insight markers are incorrectly ordered."
        )

    section = create_readme_section(insight)

    updated = (
        content[:start_index]
        + START_MARKER
        + "\n\n"
        + section
        + "\n\n"
        + END_MARKER
        + content[end_index + len(END_MARKER):]
    )

    README_FILE.write_text(
        updated,
        encoding="utf-8",
    )

    logger.info(
        "README updated successfully."
    )


# ============================================================
# OUTPUT VALIDATION
# ============================================================

def validate_outputs() -> None:
    """Perform final output integrity checks."""

    with JSON_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = json.load(file)

    required_fields = {
        "title",
        "category",
        "explanation",
        "takeaway",
        "date",
    }

    if not required_fields.issubset(data):
        raise ValueError(
            "Generated JSON is missing required fields."
        )

    svg_content = SVG_FILE.read_text(
        encoding="utf-8"
    )

    if "<svg" not in svg_content:
        raise ValueError(
            "Generated SVG is invalid."
        )

    if "</svg>" not in svg_content:
        raise ValueError(
            "Generated SVG is incomplete."
        )

    readme_content = README_FILE.read_text(
        encoding="utf-8"
    )

    if START_MARKER not in readme_content:
        raise ValueError(
            "README start marker is missing."
        )

    if END_MARKER not in readme_content:
        raise ValueError(
            "README end marker is missing."
        )

    if data["title"] not in readme_content:
        raise ValueError(
            "Generated title was not published to README."
        )

    logger.info(
        "All generated files passed validation."
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    logger.info("=" * 60)
    logger.info("AUTOMATED TECH INSIGHT")
    logger.info("Created by Muhammad Junaeid")
    logger.info("=" * 60)

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        logger.error(
            "GEMINI_API_KEY environment variable is missing."
        )
        return 1

    try:

        ensure_directories()

        previous_insight = load_previous_insight()

        generated = generate_insight(
            api_key=api_key,
            previous_insight=previous_insight,
        )

        insight = create_metadata(generated)

        logger.info(
            "Preparing publication: %s",
            insight["title"],
        )

        # Write the structured data.
        save_json(insight)

        # Generate publication artwork.
        save_svg(insight)

        # Update the README.
        update_readme(insight)

        # Validate the complete publication.
        validate_outputs()

        logger.info("=" * 60)
        logger.info("PUBLICATION COMPLETED SUCCESSFULLY")
        logger.info("Title: %s", insight["title"])
        logger.info("Category: %s", insight["category"])
        logger.info("Date: %s", insight["date"])
        logger.info("=" * 60)

        return 0

    except Exception:

        logger.exception(
            "TECH INSIGHT GENERATION FAILED."
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
