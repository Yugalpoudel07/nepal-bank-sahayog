"""
fy_detect.py
============
Detect a fiscal year mentioned inside a user question.

Handles BS and AD formats, Devanagari digits, Finance Act names and relative
phrases in English, Nepali and romanized Nepali, e.g.

    "2081/82", "FY 2080-81", "fiscal year 2025/26", "Finance Act 2083",
    "last year", "this fiscal year", "आर्थिक वर्ष २०८२/८३", "गत आर्थिक वर्ष".

When a question mentions several fiscal years ("from 2081/82 to 2082/83")
the latest one is the primary FY; all of them are returned for context.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from app.fiscal_year import AD_TO_BS_FY_OFFSET, current_fy_start, parse_fy, to_ascii_digits

_RANGE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[/\-–]\s*(\d{4}|\d{2})(?!\d)")
_FINANCE_ACT_RE = re.compile(
    r"(?:finance\s+(?:act|bill)|budget|आर्थिक\s+ऐन|आर्थिक\s+विधेयक|बजेट|arthik\s+ain)\s*,?\s*((?:19|20)\d{2})(?!\s*[/\-–]\s*\d)",
    re.IGNORECASE,
)
_FY_PREFIX_RE = re.compile(
    r"(?:\bfy\b\.?|\bf\.y\.|fiscal\s+year|आर्थिक\s+वर्ष|आ\.\s*व\.|arthik\s+barsa?)\s*:?\s*((?:19|20)\d{2})(?!\s*[/\-–]\s*\d)",
    re.IGNORECASE,
)
_RELATIVE: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\b(?:last|previous|prior)\s+(?:fiscal\s+|financial\s+|tax\s+)?year\b", re.I), -1),
    (re.compile(r"गत\s+(?:आर्थिक\s+)?वर्ष|अघिल्लो\s+(?:आर्थिक\s+)?वर्ष|पोहोर"), -1),
    (re.compile(r"\bgat(?:a)?\s+(?:arthik\s+)?(?:barsa|varsa|barsha|varsha)\b", re.I), -1),
    (re.compile(r"\bnext\s+(?:fiscal\s+|financial\s+|tax\s+)?year\b", re.I), 1),
    (re.compile(r"आगामी\s+(?:आर्थिक\s+)?वर्ष"), 1),
    (re.compile(r"\b(?:this|current|present)\s+(?:fiscal\s+|financial\s+|tax\s+)?year\b", re.I), 0),
    (re.compile(r"चालु\s+(?:आर्थिक\s+)?वर्ष|यो\s+(?:आर्थिक\s+)?वर्ष|यस\s+(?:आर्थिक\s+)?वर्ष"), 0),
]


@dataclass
class FYDetection:
    """Result of FY detection: the primary FY start year plus every FY mentioned."""

    start: int
    mentioned: list[int] = field(default_factory=list)
    matched_text: str = ""


def _single_year_to_fy(year: int) -> int:
    return year + AD_TO_BS_FY_OFFSET if year < 2060 else year


def detect_fy(question: str, today: dt.date | None = None) -> FYDetection | None:
    """Return the fiscal year referenced in the question, or None if none is found."""
    text = to_ascii_digits(question)
    found: list[tuple[int, str]] = []

    for m in _RANGE_RE.finditer(text):
        start = parse_fy(f"{m.group(1)}/{m.group(2)}")
        if start is not None:
            found.append((start, m.group(0)))
    for m in _FINANCE_ACT_RE.finditer(text):
        found.append((_single_year_to_fy(int(m.group(1))), m.group(0)))
    for m in _FY_PREFIX_RE.finditer(text):
        found.append((_single_year_to_fy(int(m.group(1))), m.group(0)))

    if not found:
        base = current_fy_start(today)
        for pattern, delta in _RELATIVE:
            m = pattern.search(text)
            if m:
                found.append((base + delta, m.group(0)))
                break

    if not found:
        return None
    starts = sorted({s for s, _ in found})
    primary = max(found, key=lambda item: item[0])
    return FYDetection(start=primary[0], mentioned=starts, matched_text=primary[1])
