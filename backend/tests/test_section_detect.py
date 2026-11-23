"""Directive / section detection in English, Nepali digits and act names."""

from __future__ import annotations

import pytest

from app.acts import canonical_section_no, normalize_ref, ref_label
from app.section_detect import SectionRef, detect_sections


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What does section 30 say?", [SectionRef(None, "s", "30")]),
        ("BAFIA section 30", [SectionRef("bafia", "s", "30")]),
        ("नेपाल राष्ट्र बैंक ऐन दफा ७९", [SectionRef("nrb_act", "s", "79")]),
        ("बैंक तथा वित्तीय संस्था सम्बन्धी ऐनको दफा ११क", [SectionRef("bafia", "s", "11A")]),
        ("directive 2", [SectionRef(None, "D", "2")]),
        ("निर्देशन नं. २ को बुँदा ९", [SectionRef(None, "D", "2.9")]),
        ("इ.प्रा. निर्देशन नं. १४/०८२", [SectionRef(None, "D", "14")]),
        ("microfinance directive 2 point 1", [SectionRef("mfi", "D", "2.1")]),
        ("payment system directive no. 5", [SectionRef("psd", "D", "5")]),
    ],
)
def test_detect_sections(question: str, expected: list[SectionRef]) -> None:
    assert detect_sections(question) == expected


@pytest.mark.parametrize("question", ["What is the cash transaction limit?", "Pay Rs. 5,000 as fee",
                                      "Mr. 5 asked about loans"])
def test_no_false_positives(question: str) -> None:
    assert detect_sections(question) == []


def test_canonical_numbers_and_refs() -> None:
    assert canonical_section_no("११क") == "11A"
    assert normalize_ref("bafia s.30") == "bafia s.30"
    assert normalize_ref("ud_abc_2082 D.2.9") == "ud_abc_2082 D.2.9"
    assert normalize_ref("ud_xyz_2082 D.2") is None
    assert normalize_ref("customs_act s.3") is None
    assert ref_label("ud_abc_2082 D.2") == "Unified Directive (class A/B/C banks) 2082, Directive 2"
