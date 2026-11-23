"""
section_detect.py
=================
Detect explicit references to an Act section or a unified-directive number in a question so the
engine can fetch that provision directly via metadata.

Examples that are detected:
    "section 30", "s.30", "BAFIA section 30", "दफा ३०", "नेपाल राष्ट्र बैंक ऐन दफा ७९",
    "directive 2", "निर्देशन नं. २", "इ.प्रा. निर्देशन नं. २/०८२", "directive 2 point 1", "निर्देशन २ को बुँदा ३".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.acts import canonical_section_no, detect_act, detect_family
from app.fiscal_year import to_ascii_digits

_NUM = r"(\d{1,3}(?:[A-Za-z](?![a-z])|[कखगघङ])?)"
_SECTION_RE = re.compile(rf"(?:\bsections?\b|\bsec\.?|(?<![A-Za-z])s\.\s?|दफा|dafa)\s*{_NUM}", re.IGNORECASE)
_DIRECTIVE_RE = re.compile(
    r"(?:\bdirectives?\b|\bud\b|निर्देशन|nirdeshan)\s*(?:no\.?|number|नं\s*\.?)?\s*(\d{1,2})(?:\s*/\s*0?\d{2,4})?"
    r"(?:\s*(?:,|को|ko)?\s*(?:\bpoint\b|\bclause\b|बुँदा|बुदा|bunda)\s*(?:no\.?|नं\s*\.?)?\s*(\d{1,2}))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SectionRef:
    """A provision explicitly named in a question."""

    act_key: str | None  # act key, a directive family ("abc", "psd", ...), or None when not stated
    kind: str  # "s" for an Act section, "D" for a directive (number "2" or point "2.1")
    number: str


def detect_sections(question: str) -> list[SectionRef]:
    """Return every section/directive reference found in the question (deduplicated, in order)."""
    text = to_ascii_digits(question)
    refs: list[SectionRef] = []
    act = detect_act(text)
    for m in _SECTION_RE.finditer(text):
        refs.append(SectionRef(act, "s", canonical_section_no(m.group(1))))
    family = detect_family(text)
    for m in _DIRECTIVE_RE.finditer(text):
        number = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
        refs.append(SectionRef(family, "D", number))

    seen: set[SectionRef] = set()
    unique: list[SectionRef] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique
