from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai
from pydantic import BaseModel, Field, ValidationError


ROOT = Path(__file__).resolve().parents[1]

TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

README = ROOT / "README.md"
JSON_OUT = ROOT / "data/latest-insight.json"
SVG_OUT = ROOT / "assets/ai-tech-insight.svg"
HISTORY = ROOT / "data/insight-history.json"

START = "<!-- INSIGHT:START -->"
END = "<!-- INSIGHT:END -->"

CATEGORIES = [
    "Software Engineering",
    "Cybersecurity",
    "Networking",
    "Cloud Computing",
    "Artificial Intelligence",
    "Web Development",
    "Developer Tools",
    "Data Engineering",
    "Operating Systems",
    "Emerging Technology",
    "Systems Design",
    "Open Source",
]


class Insight(BaseModel):
    title: str = Field(min_length=4, max_length=90)
    category: str = Field(min_length=3, max_length=40)
    explanation: str = Field(min_length=100, max_length=900)
    takeaway: str = Field(min_length=25, max_length=220)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent
    )

    try:
        with os.fdopen(
            fd, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, path)

    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_history() -> list:
    if not HISTORY.exists():
        return []

    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    except (OSError, json.JSONDecodeError):
        print("Warning: history unreadable.", file=sys.stderr)
        return []


def title_key(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        value.casefold()
    ).strip()


def generate(history: list) -> Insight:

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. "
            "Add it as a GitHub Actions repository secret."
        )

    recent = [
        item.get("title", "")
        for item in history[-30:]
    ]

    prompt = f"""
You are the technical editor of an independent developer journal.

Create one original, specific, technically accurate technology insight.

Requirements:

1. Select a concrete protocol, engineering practice,
   security concept, architecture, developer tool,
   systems concept, or emerging technology.

2. Avoid repeating or lightly rephrasing previous titles.

3. Explain what it is, how it works, and why it matters.

4. Mention an important limitation or trade-off where relevant.

5. Never invent benchmarks, dates, statistics, CVEs,
   standards, or product capabilities.

6. Avoid exaggerated claims.

7. Distinguish protocol guarantees from implementation details.

8. Select the narrowest accurate category from this list:
   {", ".join(CATEGORIES)}

9. Explanation: approximately 55-100 words.

10. Takeaway: one practical sentence.

11. Do not include Markdown, citations, sources,
    dates, or journal branding in the fields.

12. Return only structured JSON.

Previously published titles:
{chr(10).join("- " + x for x in recent) or "- None"}
""".strip()

    client = genai.Client(api_key=api_key)

    for attempt in range(2):

        request_prompt = prompt

        if attempt:
            request_prompt += (
                "\nYour previous response repeated a topic. "
                "Choose a clearly different technical subject."
            )

        response = client.interactions.create(
            model=MODEL,
            input=request_prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": Insight.model_json_schema(),
            },
        )

        output = getattr(response, "output_text", None)

        if not output:
            raise RuntimeError("Gemini returned an empty response.")

        try:
            result = Insight.model_validate_json(output)

        except (ValidationError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid Gemini response: {exc}"
            ) from exc

        category = next(
            (
                item for item in CATEGORIES
                if item.casefold() == result.category.casefold()
            ),
            None
        )

        if not category:
            raise RuntimeError(
                f"Unsupported category: {result.category}"
            )

        result = Insight(
            title=" ".join(result.title.split()),
            category=category,
            explanation=" ".join(result.explanation.split()),
            takeaway=" ".join(result.takeaway.split()),
        )

        word_count = len(result.explanation.split())

        if not 45 <= word_count <= 125:
            raise RuntimeError(
                "Explanation must contain 45-125 words."
            )

        previous_titles = {
            title_key(item)
            for item in recent
        }

        if title_key(result.title) not in previous_titles:
            return result

    raise RuntimeError(
        "Repeated topic detected twice. Publication cancelled."
    )


def escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def wrap(value: str, width: int, limit: int) -> list:

    lines = textwrap.wrap(
        value,
        width=width,
        break_long_words=False,
        break_on_hyphens=False
    )

    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"

    return lines


def text_block(
    lines: list,
    x: int,
    y: int,
    size: int,
    color: str,
    weight: int = 400,
    gap: int | None = None
) -> str:

    gap = gap or int(size * 1.45)

    spans = "".join(
        f'<tspan x="{x}" dy="{0 if i == 0 else gap}">'
        f'{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )

    return (
        f'<text x="{x}" y="{y}" font-size="{size}" '
        f'fill="{color}" font-weight="{weight}">'
        f'{spans}</text>'
    )


def build_svg(item: dict, fingerprint: str) -> str:

    seed = int(
        hashlib.sha256(
            (item["title"] + item["date"]).encode()
        ).hexdigest()[:8],
        16
    )

    rng = random.Random(seed)

    palettes = [
        ("#72E6D1", "#7C8CFF", "#F5C66A"),
        ("#63D8FF", "#A78BFA", "#6EE7B7"),
        ("#FF8FAD", "#FFC46B", "#8DE1D0"),
        ("#A3E635", "#38BDF8", "#C4B5FD"),
        ("#F0ABFC", "#67E8F9", "#FDE68A"),
        ("#FB923C", "#F472B6", "#93C5FD"),
    ]

    accent, secondary, tertiary = palettes[
        seed % len(palettes)
    ]

    title = text_block(
        wrap(item["title"], 25, 2),
        220, 500, 104, "#F8FAFC", 760, 118
    )

    explanation = text_block(
        wrap(item["explanation"], 68, 5),
        220, 755, 38, "#D7E1F2", 420, 57
    )

    takeaway = text_block(
        wrap(item["takeaway"], 66, 2),
        280, 1190, 34, "#F8FAFC", 620, 52
    )

    particles = []

    for _ in range(82):

        particles.append(
            f'<circle class="particle" '
            f'cx="{rng.randint(80,3760)}" '
            f'cy="{rng.randint(80,2040)}" '
            f'r="{rng.choice([2,3,4,5,6])}" '
            f'fill="{rng.choice([accent,secondary,tertiary])}" '
            f'style="animation-delay:-{rng.randint(0,14)}s"/>'
        )

    waves = []

    for row in range(9):

        phase = rng.random() * 6.28
        amplitude = rng.randint(45, 95)

        points = " ".join(
            f"{2350 + step * 22},"
            f"{1080 + row * 47 + math.sin(step * .22 + phase) * amplitude:.1f}"
            for step in range(61)
        )

        waves.append(
            f'<polyline points="{points}" fill="none" '
            f'stroke="{[accent,secondary,tertiary][row%3]}" '
            f'stroke-width="{7 if row%3==0 else 3}" '
            f'opacity=".62" class="wave"/>'
        )

    date = item["date"].replace("T", " · ")

    return f"""<svg xmlns="http://www.w3.org/2000/svg"
 width="3840" height="2160" viewBox="0 0 3840 2160"
 role="img" aria-labelledby="svg-title svg-desc">

<title id="svg-title">{escape(item["title"])}</title>
<desc id="svg-desc">{escape(item["explanation"])}</desc>

<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0%" stop-color="#070B16"/>
  <stop offset="55%" stop-color="#111A2D"/>
  <stop offset="100%" stop-color="#17132A"/>
 </linearGradient>

 <linearGradient id="accent" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0%" stop-color="{accent}"/>
  <stop offset="52%" stop-color="{secondary}"/>
  <stop offset="100%" stop-color="{tertiary}"/>
 </linearGradient>

 <radialGradient id="glow">
  <stop offset="0%" stop-color="{accent}" stop-opacity=".30"/>
  <stop offset="55%" stop-color="{secondary}" stop-opacity=".10"/>
  <stop offset="100%" stop-color="#070B16" stop-opacity="0"/>
 </radialGradient>

 <pattern id="grid" width="64" height="64"
  patternUnits="userSpaceOnUse">
  <circle cx="2" cy="2" r="1.8"
   fill="#9AAAC5" opacity=".14"/>
 </pattern>

 <clipPath id="clip">
  <rect width="3840" height="2160" rx="48"/>
 </clipPath>
</defs>

<style>
 .particle {{
  opacity:.18;
  animation:float 8s ease-in-out infinite alternate;
 }}

 .wave {{
  stroke-dasharray:18 26;
  animation:flow 13s linear infinite;
 }}

 .motion {{
  transform-box:fill-box;
  transform-origin:center;
  animation:breathe 9s ease-in-out infinite alternate;
 }}

 @keyframes float {{
  from {{transform:translateY(0);opacity:.15}}
  to {{transform:translateY(-28px);opacity:.72}}
 }}

 @keyframes flow {{
  to {{stroke-dashoffset:-352}}
 }}

 @keyframes breathe {{
  from {{transform:scale(.94);opacity:.65}}
  to {{transform:scale(1.08);opacity:1}}
 }}

 @media(prefers-reduced-motion:reduce) {{
  .particle,.wave,.motion {{animation:none}}
 }}
</style>

<g clip-path="url(#clip)">

 <rect width="3840" height="2160" fill="url(#bg)"/>
 <rect width="3840" height="2160" fill="url(#grid)"/>

 <circle cx="3150" cy="900" r="1200"
  fill="url(#glow)" class="motion"/>

 <circle cx="3650" cy="150" r="820"
  fill="url(#glow)" class="motion"/>

 <path d="M2250 0L3840 1080M2400 2160L3840 1020"
  stroke="url(#accent)" stroke-width="4" opacity=".12"/>

 <g>{''.join(particles)}</g>

 <g font-family="Inter,Segoe UI,Arial,sans-serif">

  <text x="220" y="190" font-size="54"
   font-weight="800" letter-spacing="5" fill="#F8FAFC">
   JUNAEID <tspan fill="{accent}">/</tspan> TECH JOURNAL
  </text>

  <rect x="220" y="270" width="10" height="52"
   rx="5" fill="url(#accent)"/>

  <text x="255" y="310" font-size="34"
   font-weight="750" letter-spacing="4" fill="{accent}">
   {escape(item["category"].upper())}
  </text>

  {title}

  <line x1="220" y1="625" x2="2140" y2="625"
   stroke="#94A3B8" opacity=".30" stroke-width="3"/>

  {explanation}

  <rect x="220" y="1050" width="1940" height="250"
   rx="30" fill="#FFF" fill-opacity=".055"
   stroke="#FFF" stroke-opacity=".14" stroke-width="2"/>

  <rect x="220" y="1050" width="10" height="250"
   rx="5" fill="url(#accent)"/>

  <text x="280" y="1135" font-size="30"
   font-weight="800" letter-spacing="4" fill="{accent}">
   KEY TAKEAWAY
  </text>

  {takeaway}

  <line x1="220" y1="1990" x2="3610" y2="1990"
   stroke="#94A3B8" opacity=".25" stroke-width="2"/>

  <text x="220" y="2060" font-size="28"
   font-weight="750" letter-spacing="2" fill="#E2E8F0">
   CREATED BY MUHAMMAD JUNAEID
  </text>

  <text x="3610" y="2060" font-size="24"
   text-anchor="end" fill="#A5B4FC">
   {escape(date)}
  </text>

 </g>

 <g transform="translate(2280 250)">

  <circle cx="650" cy="760" r="680" fill="url(#glow)"/>

  <circle cx="650" cy="760" r="610"
   fill="none" stroke="url(#accent)" stroke-width="3"
   opacity=".24" stroke-dasharray="12 24" class="motion"/>

  {''.join(waves)}

 </g>

 <text x="3610" y="2120" text-anchor="end"
  font-family="Inter,Segoe UI,Arial,sans-serif"
  font-size="22" font-weight="650" letter-spacing="4"
  fill="{accent}">
  DIGITAL INTELLIGENCE JOURNAL · {fingerprint[:10].upper()}
 </text>

</g>
</svg>
"""


def prepare_readme(item: dict, fingerprint: str) -> str:

    source = README.read_text(encoding="utf-8")

    if source.count(START) != 1 or source.count(END) != 1:
        raise RuntimeError(
            "README must contain exactly one pair of insight markers."
        )

    begin = source.index(START) + len(START)
    finish = source.index(END)

    if finish <= begin:
        raise RuntimeError("README markers are out of order.")

    block = f"""

<div align="center">

### AUTOMATED TECH INSIGHT OF THE DAY

**{item["title"]}**

`{item["category"]}` · `{item["date"]}`

![Junaeid Tech Journal](assets/ai-tech-insight.svg?v={fingerprint})

</div>

**The insight**

{item["explanation"]}

> **KEY TAKEAWAY**
>
> {item["takeaway"]}

<sub>Created by Muhammad Junaeid · AI-assisted technology journal</sub>

"""

    return source[:begin] + block + source[finish:]


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate a sample publication without calling Gemini."
    )

    args = parser.parse_args()

    history = load_history()

    if args.dry_run:

        insight = Insight(
            title="TCP Fast Open",
            category="Networking",
            explanation=(
                "TCP Fast Open is an extension that can let a client "
                "include application data in the initial TCP SYN, reducing "
                "the wait before a request can begin. A server issues a "
                "cookie that a client can present on a later connection. "
                "The server must support and enable the feature, and data "
                "sent before the handshake completes has replay-related "
                "security considerations."
            ),
            takeaway=(
                "Evaluate TCP Fast Open on supported client and server "
                "paths, and account for replay risks, middlebox "
                "compatibility, and real workload measurements."
            )
        )

    else:
        insight = generate(history)

    local_now = datetime.now(ZoneInfo(TIMEZONE))

    item = {
        "title": insight.title,
        "category": insight.category,
        "explanation": insight.explanation,
        "takeaway": insight.takeaway,
        "date": local_now.isoformat(timespec="seconds"),
    }

    canonical = json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True
    )

    fingerprint = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:16]

    svg = build_svg(item, fingerprint)

    # Validate before publishing.
    ET.fromstring(svg)

    # Validate README markers before writing other files.
    updated_readme = prepare_readme(item, fingerprint)

    atomic_write(
        JSON_OUT,
        json.dumps(item, ensure_ascii=False, indent=2) + "\n"
    )

    atomic_write(SVG_OUT, svg)
    atomic_write(README, updated_readme)

    history.append({
        "title": item["title"],
        "date": item["date"]
    })

    atomic_write(
        HISTORY,
        json.dumps(history[-30:], ensure_ascii=False, indent=2) + "\n"
    )

    print(f"Published: {item['title']}")
    print(f"Category: {item['category']}")
    print(f"Timestamp: {item['date']}")

    return 0


if __name__ == "__main__":

    try:
        raise SystemExit(main())

    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
