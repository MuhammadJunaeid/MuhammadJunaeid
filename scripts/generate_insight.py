from __future__ import annotations
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from textwrap import wrap
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
README_FILE = ROOT / "README.md"
JSON_FILE = ROOT / "data" / "insight.json"
SVG_FILE = ROOT / "assets" / "ai-tech-insight.svg"
TIMEZONE = ZoneInfo("Asia/Dhaka")
AUTHOR = "Muhammad Junaeid"
GENERATOR = "Junaeid Tech Journal"

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
FALLBACK_MODELS = [
    item.strip()
    for item in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.5-flash-lite,gemini-2.5-flash-lite",
    ).split(",")
    if item.strip()
]
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT = max(10, int(os.getenv("GEMINI_TIMEOUT", "75")))
MAX_ATTEMPTS = max(1, min(4, int(os.getenv("GEMINI_MAX_ATTEMPTS", "2"))))
RETRY_BASE_SECONDS = max(1.0, float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "3")))
MAX_RETRY_WAIT = 45.0

START_MARKER = "<!-- INSIGHT:START -->"
END_MARKER = "<!-- INSIGHT:END -->"

ALLOWED_CATEGORIES = [
    "Artificial Intelligence",
    "Web Development",
    "Cybersecurity",
    "Software Engineering",
    "Systems & Architecture",
    "Emerging Technology",
    "Developer Productivity",
    "Cloud Computing",
    "Data & Privacy",
]

CATEGORY_ALIASES = {
    "ai": "Artificial Intelligence",
    "artificial intelligence": "Artificial Intelligence",
    "machine learning": "Artificial Intelligence",
    "ml": "Artificial Intelligence",
    "web": "Web Development",
    "web development": "Web Development",
    "frontend development": "Web Development",
    "front end development": "Web Development",
    "backend development": "Web Development",
    "back end development": "Web Development",
    "cyber security": "Cybersecurity",
    "cybersecurity": "Cybersecurity",
    "information security": "Cybersecurity",
    "software engineering": "Software Engineering",
    "software development": "Software Engineering",
    "systems and architecture": "Systems & Architecture",
    "systems architecture": "Systems & Architecture",
    "system architecture": "Systems & Architecture",
    "systems architecture design": "Systems & Architecture",
