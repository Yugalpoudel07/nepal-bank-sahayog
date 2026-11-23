"""
preeti.py
=========
Detect and convert legacy Preeti / Kantipur-font Nepali to Unicode Devanagari.

Many older Nepali government PDFs were typeset with the Preeti (or the
layout-identical Kantipur) font: the PDF stores *Latin* characters that only
look like Devanagari when rendered with that font. Extracted text therefore
looks like gibberish (e.g. "cfos/ P]g" instead of "आयकर ऐन").

Conversion = character mapping + re-ordering rules (the short-i matra is typed
before its consonant and the reph "र्" after its syllable in Preeti).
This is a best-effort converter; always spot-check converted documents.
"""

from __future__ import annotations

import re

# Single-character map (Preeti keyboard layout). Multi-glyph outputs are fine.
PREETI_MAP: dict[str, str] = {
    "a": "ब", "b": "द", "c": "अ", "d": "म", "e": "भ", "f": "ा", "g": "न", "h": "ज", "i": "ष्",
    "j": "व", "k": "प", "l": "ि", "m": "ः", "n": "ल", "o": "य", "p": "उ", "q": "त्र", "r": "च",
    "s": "क", "t": "त", "u": "ग", "v": "ख", "w": "ध", "x": "ह", "y": "थ", "z": "श",
    "A": "ब्", "B": "द्य", "C": "ऋ", "D": "म्", "E": "भ्", "F": "ँ", "G": "न्", "H": "ज्",
    "I": "क्ष्", "J": "व्", "K": "प्", "L": "ी", "M": "ः", "N": "ल्", "O": "इ", "P": "ए",
    "Q": "त्त", "R": "च्", "S": "क्", "T": "त्", "U": "ग्", "V": "ख्", "W": "ध्", "X": "ह्",
    "Y": "थ्", "Z": "श्",
    "0": "ण्", "1": "ज्ञ", "2": "द्द", "3": "घ", "4": "द्ध", "5": "छ", "6": "ट", "7": "ठ",
    "8": "ड", "9": "ढ",
    "!": "१", "@": "२", "#": "३", "$": "४", "%": "५", "^": "६", "&": "७", "*": "८", "(": "९",
    ")": "०",
    "`": "ञ", "~": "ञ्", "-": "(", "_": ")", "=": ".", "+": "ं", "[": "ृ", "]": "े",
    "{": "र्", "}": "ै", "\\": "्", "|": "्र", ";": "स", ":": "स्", "'": "ु", '"': "ू",
    ",": ",", "<": "?", ".": "।", ">": "श्र", "/": "र", "?": "रु",
    "«": "्र", "Ø": "्य", "¿": "रू", "Í": "ङ्क", "Î": "ङ्ख", "Ë": "ङ्ग", "Ì": "न्न",
    "Ý": "ट्ठ", "å": "द्व", "ç": "ॐ", "÷": "/", "ß": "द्म", "§": "ट्ट", "•": "ड्ड",
    "Å": "हृ", "Ç": "ङ्घ", "ˆ": "फ्", "ø": "य्", "Ö": "=", "Ù": ";", "Ú": "’", "Û": "!",
    "Ü": "%", "Æ": "”", "æ": "“", "¥": "र्\u200d", "‘": "ॅ", "¶": "ठ्ठ", "´": "झ", "£": "घ्",
    "¤": "झ्", "°": "ङ्क", "Ł": "ङ्ग", "ª": "ङ",
}

_CONSONANT = "[\u0915-\u0939\u0958-\u095F]"
_MATRA = "[ािीुूृेैोौंःँ]"
_PREETI_HINT_RE = re.compile(r"[{}\[\]|\\;:/]|f]|\bg]|cf|df|sf")


def looks_like_preeti(text: str) -> bool:
    """
    Heuristic: text that should be Nepali but contains almost no Devanagari and
    many Preeti-typical Latin symbol patterns.
    """
    letters = [c for c in text if not c.isspace()]
    if len(letters) < 40:
        return False
    devanagari = sum(1 for c in letters if "\u0900" <= c <= "\u097F")
    if devanagari / len(letters) > 0.2:
        return False
    hints = len(_PREETI_HINT_RE.findall(text))
    words = max(1, len(text.split()))
    return hints / words > 0.25


def preeti_to_unicode(text: str) -> str:
    """Convert Preeti-encoded text to Unicode Devanagari."""
    # Glyph pairs that form one letter in Preeti (must run before the single-character map).
    for pair, letter in (("pm", "ऊ"), ("qm", "क्र"), ("Qm", "क्त"), ("km", "फ"), ("em", "झ")):
        text = text.replace(pair, letter)
        text = text.replace(pair[0] + "{" + pair[1], letter + "{")  # reph typed between the pair: "dfk{mt" = मार्फत
    out = "".join(PREETI_MAP.get(ch, ch) for ch in text)

    # Half-form + aa-matra means the full consonant: "क्ष्ा" -> "क्ष"
    out = out.replace("्ा", "")
    # Independent vowels typed as base + matra
    out = (
        out.replace("अा", "आ").replace("आे", "ओ").replace("आै", "औ").replace("अो", "ओ").replace("अौ", "औ")
        .replace("ाे", "ो").replace("ाै", "ौ").replace("एे", "ऐ").replace("इर्", "ई")
    )
    # Short-i matra precedes its (possibly conjunct) consonant in Preeti: move it after.
    out = re.sub(rf"ि((?:{_CONSONANT}्)*{_CONSONANT}[़]?)", r"\1ि", out)
    # Reph is typed after the syllable it sits on: move "र्" before that syllable.
    out = re.sub(rf"((?:{_CONSONANT}्)*{_CONSONANT}[़]?{_MATRA}*)र्(?!\u200d)", r"र्\1", out)
    out = out.replace("\u200d", "")
    return out
