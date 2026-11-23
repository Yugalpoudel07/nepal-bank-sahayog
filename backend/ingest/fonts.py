"""
fonts.py
========
Font-aware handling of legacy Nepali fonts in PDFs.

NRB documents mix Unicode text (English words, Kalimati/Mangal Nepali) with text typed in
legacy fonts. Converting a whole page with the Preeti map would garble the Unicode parts
("ICAAP" -> "क्ष्ऋब्ब्ए"), so conversion happens per character run, using the font name
pdfplumber reports for every glyph:

* Preeti-layout text fonts (Preeti, Himalb, Kantipur) -> full Preeti-to-Unicode conversion.
* "Numbering" fonts (Fontasy Himali, Bishall, Himalli, Himchuli, Himalaya) are used almost only
  for list numbers and dates in these PDFs -> digits become Devanagari digits, "=" a full stop.

It also detects the broken text layer of Law-Commission-style PDFs, where the Unicode text is
present but the glyph order is wrong ("मिलाइएको" extracts as "ममलाइएको", "गर्ने" as "गने").
Those documents must be OCR'd.
"""

from __future__ import annotations

import re

from ingest.preeti import preeti_to_unicode

MARK = "\ue000"  # private-use marker put in front of every glyph typed in a Preeti-layout font
TEXT_FONTS = ("preeti", "himalb", "kantipur", "sagarmatha")
NUMBER_FONTS = ("fontasy", "himali", "himalli", "bishal", "himchuli", "himalaya")
_DEV_DIGITS = "०१२३४५६७८९"
_NUMBER_PUNCT = {"=": ".", "-": "(", "_": ")"}
_RUN_RE = re.compile(rf"(?:{MARK}[^\n](?: +(?={MARK}))?)+")


def font_family(fontname: str) -> str | None:
    """'preeti', 'number' or None (Unicode font) for a PDF font name such as 'ABCDEF+Preeti-Bold'."""
    f = re.sub(r"[\s_\-,]", "", fontname.split("+")[-1].lower())
    if any(k in f for k in TEXT_FONTS):
        return "preeti"
    if any(k in f for k in NUMBER_FONTS):
        return "number"
    return None


def mark_page(page) -> int:
    """
    Tag the glyphs of a pdfplumber page in place, before any text extraction:
    Preeti glyphs get MARK in front, numbering-font digits become Devanagari digits.
    Returns the number of Preeti glyphs.
    """
    n = 0
    for ch in page.chars:
        fam = font_family(ch.get("fontname", ""))
        if fam == "preeti":
            ch["text"] = MARK + ch["text"]
            n += 1
        elif fam == "number":
            t = ch["text"]
            if t.isdigit() and t.isascii():
                ch["text"] = _DEV_DIGITS[int(t)]
            elif t in _NUMBER_PUNCT:
                ch["text"] = _NUMBER_PUNCT[t]
    return n


def convert_marked(text: str) -> str:
    """Convert every marked run (marked glyphs, possibly separated by plain spaces) to Unicode."""
    if MARK not in text:
        return text
    out = _RUN_RE.sub(lambda m: preeti_to_unicode(m.group(0).replace(MARK, "")), text)
    return out.replace(MARK, "")


_LAW_GARBLE_RE = re.compile(
    r"(?<![ऀ-ॿ])(?:गने|पने|गनध|गनन|गनुन|गनुध|गदाध|गदान|ममलाइएको|अमधकार\S*|ववषय|वववरण|मनयम\S*|सम्बक्तन्धत)"
    r"(?![ऀ-ॿ])"
)
LAW_GARBLE_THRESHOLD = 0.5  # garbled words per 1000 Devanagari words; clean NRB text scores 0


def law_garble_score(text: str) -> float:
    """Per-mille of words that only occur in mis-ordered (broken) Unicode text layers."""
    words = len(re.findall("[ऀ-ॿ]+", text))
    return 0.0 if words < 200 else len(_LAW_GARBLE_RE.findall(text)) * 1000 / words
