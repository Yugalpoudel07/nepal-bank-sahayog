"""
acts.py
=======
Identifiers for the base documents (Acts and unified-directive editions) and the
reference format used to link circulars to the provisions they amend.

A reference string is ``"<doc_key> <kind>.<number>"``:

    "bafia s.30"         section 30 of the Bank and Financial Institutions Act
    "ud_abc_2082 D.2"    Directive No. 2 of the 2082 Unified Directive for class A/B/C banks
    "ud_psd_2082 D.5"    Directive No. 5 of the 2082 Payment System Unified Directive

Acts are a fixed list (aliases let us recognise them in questions). Directive editions are
derived from family + edition year (see ingest/manifest_tools.py), so a new edition needs no code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.fiscal_year import to_ascii_digits

# act_key -> (short English name, Nepali name)
ACTS: dict[str, tuple[str, str]] = {
    "bafia": ("Bank and Financial Institutions Act 2073", "बैंक तथा वित्तीय संस्था सम्बन्धी ऐन, २०७३"),
    "nrb_act": ("Nepal Rastra Bank Act 2058", "नेपाल राष्ट्र बैंक ऐन, २०५८"),
    "payment_act": ("Payment and Settlement Act 2075", "भुक्तानी तथा फर्स्यौट ऐन, २०७५"),
    "bopa": ("Banking Offence and Punishment Act 2064", "बैंकिङ्ग कसूर तथा सजाय ऐन, २०६४"),
    "mlpa": ("Money Laundering Prevention Act 2064", "सम्पत्ति शुद्धीकरण (मनी लाउन्डरिङ्ग) निवारण ऐन, २०६४"),
    "fx_act": ("Foreign Exchange (Regulation) Act 2019", "विदेशी विनिमय (नियमित गर्ने) ऐन, २०१९"),
    "ni_act": ("Negotiable Instruments Act 2034", "विनिमेय अधिकारपत्र ऐन, २०३४"),
    "debt_recovery_act": ("BFI Debt Recovery Act 2058", "बैङ्क तथा वित्तीय संस्थाको ऋण असुली ऐन, २०५८"),
    "consumer_act": ("Consumer Protection Act 2075", "उपभोक्ता संरक्षण ऐन, २०७५"),
    "privacy_act": ("Privacy Act 2075", "वैयक्तिक गोपनीयता सम्बन्धी ऐन, २०७५"),
}

# Regex alternatives that identify each act in free text (English, Nepali, romanized). Order matters:
# more specific names first ("NRB Act" before a bare "Act").
ACT_ALIASES: dict[str, str] = {
    "debt_recovery_act": r"debt\s+recovery\s+act|ऋण\s+असुली\s+ऐन",
    "bopa": r"banking\s+offen[cs]e|बै(?:ं|ङ्)किङ्?ग?\s+कसूर|banking\s+kasur",
    "bafia": r"bank\s+and\s+financial\s+institutions?\s+act|\bbafia\b|बै(?:ं|ङ्)क\s+तथा\s+वित्तीय\s+संस्था\s+सम्बन्धी\s+ऐन",
    "nrb_act": r"(?:nepal\s+rastra\s+bank|\bnrb)\s+act|नेपाल\s+राष्ट्र\s+बै(?:ं|ङ्)क\s+ऐन|राष्ट्र\s+बैंक\s+ऐन",
    "payment_act": r"payment\s+(?:and|&)\s+settlement\s+act|भुक्तानी\s+तथा\s+फर्स्यौट\s+ऐन",
    "mlpa": r"money\s+laundering\s+prevention\s+act|\bmlpa\b|मनी\s+लाउन्डरि(?:ङ्ग|ङ)\)?\s*निवारण\s+ऐन|सम्पत्ति\s+शुद्धीकरण",
    "fx_act": r"foreign\s+exchange\s+(?:\(?regulation\)?\s+)?act|\bfera\b|विदेशी\s+विनिमय\s+\(?नियमित",
    "ni_act": r"negotiable\s+instruments?\s+act|विनिमेय\s+अधिकारपत्र\s+ऐन",
    "consumer_act": r"consumer\s+protection\s+act|उपभोक्ता\s+संरक्षण\s+ऐन",
    "privacy_act": r"privacy\s+act|वैयक्तिक\s+गोपनीयता",
}

# Which Act a file in data/raw/acts is (matched against the file name).
ACT_FILES: dict[str, str] = {
    "bank-and-financial-instituion": "bafia", "bank-and-financial-institutions-act": "bafia",
    "nrb-act": "nrb_act", "nepal-rastra-bank-act": "nrb_act", "payment-and-settlement-act": "payment_act",
    "banking-offence": "bopa", "money-laundering": "mlpa", "foreign-exchange-regulation": "fx_act",
    "negotiable-instruments": "ni_act", "debt-recovery": "debt_recovery_act",
    "consumer-protection": "consumer_act", "privacy-act": "privacy_act",
}

FAMILY_SHORT: dict[str, tuple[str, str]] = {
    "abc": ("Unified Directive (class A/B/C banks)", "एकीकृत निर्देशन (क, ख, ग वर्ग)"),
    "mfi": ("Unified Directive (class D microfinance)", "एकीकृत निर्देशन (घ वर्ग लघुवित्त)"),
    "nifra": ("Unified Directive (infrastructure bank)", "एकीकृत निर्देशन (पूर्वाधार विकास बैंक)"),
    "psd": ("Payment System Unified Directive", "भुक्तानी प्रणालीसम्बन्धी एकीकृत निर्देशन"),
    "fx": ("Foreign Exchange Unified Circular", "विदेशी विनिमयसम्बन्धी एकीकृत परिपत्र"),
    "ncbl": ("Directive for cooperative banks", "सहकारी बैंकका लागि निर्देशन"),
}
FAMILY_ALIASES: dict[str, str] = {
    "psd": r"payment\s+system|भुक्तानी\s+प्रणाली|psp|wallet|वालेट|e-?money|ई-?मनी|qr\b",
    "fx": r"foreign\s+exchange\s+(?:unified\s+)?circular|एकीकृत\s+परिपत्र|सटही|विदेशी\s+मुद्रा",
    "mfi": r"micro\s*finance|लघुवित्त|“?घ”?\s+वर्ग|class\s+d\b",
    "nifra": r"infrastructure\s+(?:development\s+)?bank|पूर्वाधार\s+विकास\s+बैंक",
    "ncbl": r"cooperative\s+bank|सहकारी\s+बैंक",
    "abc": r"commercial\s+bank|development\s+bank|finance\s+compan|वाणिज्य\s+बैंक|विकास\s+बैंक|वित्त\s+कम्पनी|class\s+[abc]\b",
}

_SUFFIX_MAP = {"क": "A", "ख": "B", "ग": "C", "घ": "D", "ङ": "E"}
_UD_KEY_RE = re.compile(r"^ud_(abc|mfi|nifra|psd|fx|ncbl)_(20\d\d)$")
_REF_RE = re.compile(r"^(?P<key>[a-z0-9_]+)\s+(?P<kind>s|D|Sch)\.(?P<no>[0-9]+[A-Z]?(?:\.[0-9]+)?)$", re.IGNORECASE)
_KINDS = {"s": "s", "d": "D", "sch": "Sch"}


def canonical_section_no(raw: str) -> str:
    """Normalize a section number: Devanagari digits -> ASCII, Nepali suffix -> Latin, upper-case."""
    text = to_ascii_digits(raw.strip())
    for ne, en in _SUFFIX_MAP.items():
        text = text.replace(ne, en)
    return text.upper()


def act_key_for_path(path: str) -> str | None:
    """Act key from a file name in data/raw/acts ("acts/nrb-act-2058.pdf" -> "nrb_act")."""
    name = path.rsplit("/", 1)[-1].lower()
    return next((key for frag, key in ACT_FILES.items() if frag in name), None)


def doc_display_name(key: str | None, lang: str = "en") -> str | None:
    """Human name for an act key or directive-edition key."""
    if key in ACTS:
        return ACTS[key][0 if lang == "en" else 1]
    m = _UD_KEY_RE.match(key or "")
    if m:
        name = FAMILY_SHORT[m.group(1)][0 if lang == "en" else 1]
        return f"{name} {m.group(2)}"
    return None


def act_short_name(act_key: str | None) -> str | None:
    """English short name for an act key or directive edition."""
    return doc_display_name(act_key, "en")


def is_known_key(key: str | None) -> bool:
    return key in ACTS or bool(_UD_KEY_RE.match(key or ""))


def make_ref(key: str, number: str, kind: str = "s") -> str:
    """make_ref("bafia", "30") -> "bafia s.30"; make_ref("ud_abc_2082", "2", "D") -> "ud_abc_2082 D.2"."""
    if not is_known_key(key):
        raise ValueError(f"Unknown document key: {key}")
    return f"{key} {kind}.{canonical_section_no(number)}"


def ref_label(ref: str, lang: str = "en") -> str:
    """Readable form of a reference ("ud_abc_2082 D.2" -> "Unified Directive (class A/B/C banks) 2082, Directive 2")."""
    p = parse_ref(ref)
    if p is None:
        return ref
    name = doc_display_name(p.act_key, lang) or p.act_key
    word = {"s": ("Section", "दफा"), "D": ("Directive", "निर्देशन नं."), "Sch": ("Schedule", "अनुसूची")}[p.kind]
    return f"{name}, {word[0 if lang == 'en' else 1]} {p.number}"


@dataclass(frozen=True)
class ParsedRef:
    """A parsed reference string."""

    act_key: str
    kind: str  # "s" (section), "D" (directive number), "Sch" (schedule/annex)
    number: str


def parse_ref(ref: str) -> ParsedRef | None:
    """Parse "bafia s.30" back into its parts; None if not a known document key."""
    m = _REF_RE.match(ref.strip())
    key = m.group("key").lower() if m else None
    if not m or not is_known_key(key):
        return None
    return ParsedRef(key, _KINDS[m.group("kind").lower()], canonical_section_no(m.group("no")))  # type: ignore[arg-type]


def normalize_ref(ref: str) -> str | None:
    """Return the canonical form of a reference string, or None if unparseable."""
    p = parse_ref(ref)
    return f"{p.act_key} {p.kind}.{p.number}" if p else None


def _first_match(aliases: dict[str, str], text: str) -> str | None:
    best: tuple[int, str] | None = None
    for key, pattern in aliases.items():
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), key)
    return best[1] if best else None


def detect_act(text: str) -> str | None:
    """Return the first act key mentioned in the text, or None."""
    return _first_match(ACT_ALIASES, text)


def detect_family(text: str) -> str | None:
    """Which rulebook family a question is about (payment system, microfinance, ...), or None."""
    return _first_match(FAMILY_ALIASES, text)
