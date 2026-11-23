"""
heuristics.py
=============
Deterministic text heuristics shared by the chunker, the linker and the offline
(no-LLM) enrichment fallback:

* `find_refs`             — references to Act sections and unified-directive numbers inside a text
* `extract_numeric_facts` — percentage / amount statements (limits, ratios, provisioning rates)
* `keywords`              — frequent content words
* `topic_tags`            — coarse banking topic labels from a keyword dictionary

They are intentionally conservative; the optional LLM enrichment adds richer metadata.
"""

from __future__ import annotations

import re
from collections import Counter

from app.acts import ACT_ALIASES, canonical_section_no, is_known_key, make_ref
from app.fiscal_year import to_ascii_digits

_SECTION_REF_RE = re.compile(r"(?:\bsections?\s+|दफा\s*)(?P<sec>\d{1,3}(?:[A-Z](?![a-z])|[कखगघङ])?)", re.IGNORECASE)
# "इ.प्रा. निर्देशन नं. १४/०८२ को बुँदा नं. २", "निर्देशन नं. ५/०८१", "एकीकृत इ.प्रा. परिपत्र ः ७/२०८२ को बुँदा ३"
_DIRECTIVE_REF_RE = re.compile(
    r"(?:निर्देशन|परिपत्र)\s*[:ः]?\s*(?:नं\s*\.?)?\s*[:ः]?\s*(?P<no>\d{1,2})\s*/\s*(?:20)?0?(?P<yr>[78]\d)"
    r"(?:\s*(?:को|मा)?\s*(?:बुँदा|बँुदा|बुदा|दफा)\s*(?:नं\s*\.?)?\s*\(?(?P<pt>\d{1,2})\)?)?"
)
_ACT_ANY_RE = re.compile("|".join(f"(?P<{k}>{p})" for k, p in ACT_ALIASES.items()), re.IGNORECASE)
_THIS_ACT_RE = re.compile(r"\bof\s+this\s+act\b|यस\s+ऐनको", re.IGNORECASE)


def _nearest_act(text: str, pos: int, window: int = 120) -> str | None:
    best: tuple[int, str] | None = None
    lo, hi = max(0, pos - window), min(len(text), pos + window)
    for m in _ACT_ANY_RE.finditer(text, lo, hi):
        dist = abs(m.start() - pos)
        key = m.lastgroup
        if key and (best is None or dist < best[0]):
            best = (dist, key)
    return best[1] if best else None


def find_refs(text: str, default_key: str | None = None) -> list[str]:
    """
    Find references and return canonical ref strings:

    * "नेपाल राष्ट्र बैंक ऐन, २०५८ को दफा ७९" -> "nrb_act s.79" (the act must be named nearby)
    * "इ.प्रा. निर्देशन नं. १४/०८२ को बुँदा नं. २" with default_key "ud_abc_2082"
      -> "ud_abc_2082 D.14", "ud_abc_2082 D.14.2" (the family comes from default_key,
      the edition year from the reference itself)
    """
    t = to_ascii_digits(text)
    refs: list[str] = []

    def add(ref: str) -> None:
        if ref not in refs:
            refs.append(ref)

    for m in _SECTION_REF_RE.finditer(t):
        if _THIS_ACT_RE.match(t[m.end(): m.end() + 20].strip()):
            continue
        act = _nearest_act(t, m.start())
        if act:
            add(make_ref(act, canonical_section_no(m.group("sec")), "s"))

    family = default_key.split("_")[1] if default_key and default_key.startswith("ud_") else None
    if family:
        for m in _DIRECTIVE_REF_RE.finditer(t):
            key = f"ud_{family}_20{m.group('yr')}"
            if not is_known_key(key):
                continue
            add(make_ref(key, m.group("no"), "D"))
            if m.group("pt"):
                add(make_ref(key, f"{m.group('no')}.{m.group('pt')}", "D"))
    return refs


# ── numeric facts ─────────────────────────────────────────────────────────────
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|प्रतिशत)", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(?:रु\.?|Rs\.?|NPR)\s*(\d[\d,]*(?:\.\d+)?)\s*(करोड|लाख|हजार|arba|अर्ब|crore|lakh|million)?",
                        re.IGNORECASE)
_SENT_SPLIT_RE = re.compile(r"(?<=[।!?])\s+|\n|(?<!रु)(?<!Rs)(?<!नं)(?<!\d)\.\s+")


def extract_numeric_facts(text: str, fiscal_year: str | None, limit: int = 8) -> list[dict]:
    """
    Sentences and table rows that state a percentage or an amount (ratios, limits, provisioning rates).
    Used to rank "how much / what percent" questions; calculators only use rows a human verified.
    """
    facts: list[dict] = []
    t = to_ascii_digits(text)
    for piece in _SENT_SPLIT_RE.split(t):
        piece = piece.strip(" |")
        if len(piece) < 8:
            continue
        pct = _PERCENT_RE.search(piece)
        amt = _AMOUNT_RE.search(piece)
        if not (pct or amt):
            continue
        facts.append({
            "category": "percentage" if pct else "amount",
            "fiscal_year": fiscal_year,
            "rate_percent": float(pct.group(1)) if pct else None,
            "amount": amt.group(1).replace(",", "") + (f" {amt.group(2)}" if amt and amt.group(2) else "") if amt else None,
            "source_quote": re.sub(r"\s+", " ", piece)[:300],
        })
        if len(facts) >= limit:
            break
    return facts


# ── keywords & topics ─────────────────────────────────────────────────────────
_STOP = set(
    "the of and to in a an or for on by be is are shall any with as from that this which such under "
    "at it its not no may has have been was were other than into per section sub clause act rules bank "
    "को र मा ले छ हुने गर्ने वा भएको यस त्यस पनि गरी लागि सम्बन्धी बमोजिम संस्था संस्थाले इजाजतपत्रप्राप्त "
    "बैंक वित्तीय तथा गर्नु पर्नेछ देहायबमोजिम".split()
)
_TOKEN_RE = re.compile(r"[A-Za-zऀ-ॿ]{3,}")

TOPICS: dict[str, tuple[str, ...]] = {
    "capital_adequacy": ("capital adequacy", "पुँजीकोष", "पूँजीकोष", "cet1", "tier 1", "buffer"),
    "loan_classification": ("loan loss", "provision", "कर्जा नोक्सानी", "वर्गीकरण", "npl", "निष्क्रिय कर्जा", "भाखा नाघेको"),
    "credit_limits": ("single obligor", "एकल ग्राहक", "क्षेत्रगत कर्जा", "credit limit", "कर्जा सीमा"),
    "interest_rates": ("interest rate", "ब्याजदर", "base rate", "आधार दर", "spread", "प्रिमियम"),
    "liquidity": ("crr", "slr", "अनिवार्य मौज्दात", "वैधानिक तरलता", "liquidity", "तरलता", "cd ratio"),
    "governance": ("governance", "सुशासन", "सञ्चालक", "board", "director", "प्रमुख कार्यकारी"),
    "kyc_aml": ("kyc", "aml", "ग्राहक पहिचान", "सम्पत्ति शुद्धीकरण", "money laundering", "आतंकवादी"),
    "consumer_protection": ("grievance", "गुनासो", "ग्राहक संरक्षण", "customer protection", "वित्तीय साक्षरता", "शुल्क"),
    "deposits": ("deposit", "निक्षेप", "बचत", "मुद्दती", "fixed deposit"),
    "branches": ("branch", "शाखा", "कार्यालय खोल्ने"),
    "payments": ("payment", "भुक्तानी", "wallet", "वालेट", "qr", "card", "कार्ड", "e-money", "ई-मनी", "rtgs"),
    "digital_lending": ("digital lending", "विद्युतीय कर्जा", "विद्युतीय माध्यमबाट प्रवाह"),
    "foreign_exchange": ("foreign exchange", "विदेशी विनिमय", "सटही", "प्रतीतपत्र", "letter of credit", "remittance", "विप्रेषण"),
    "cash_limits": ("cash transaction", "नगद कारोबार", "नगद"),
    "monetary_policy": ("monetary policy", "मौद्रिक नीति", "policy rate", "नीतिगत दर", "inflation", "मुद्रास्फीति"),
    "merger": ("merger", "acquisition", "मर्जर", "गाभ्ने", "प्राप्ति"),
    "blacklist": ("blacklist", "कालोसूची", "credit information", "कर्जा सूचना"),
    "penalties": ("penalty", "fine", "जरिवाना", "कारबाही", "कारवाही", "सजाय"),
}


def keywords(text: str, limit: int = 12) -> list[str]:
    """Most frequent non-stopword tokens (≥3 chars)."""
    words = [w.lower() for w in _TOKEN_RE.findall(text)]
    counts = Counter(w for w in words if w not in _STOP)
    return [w for w, _ in counts.most_common(limit)]


def topic_tags(text: str) -> list[str]:
    """Coarse topic labels based on keyword presence."""
    low = text.lower()
    return [t for t, keys in TOPICS.items() if any(re.search(rf"(?<![a-z]){re.escape(k)}", low) for k in keys)]
