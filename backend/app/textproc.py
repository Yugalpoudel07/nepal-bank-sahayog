"""
textproc.py
===========
Text helpers shared by indexing and retrieval so both sides tokenize the same way.
"""

from __future__ import annotations

import re

from app.fiscal_year import to_ascii_digits

# Devanagari vowel signs are not matched by \w, so include the whole block explicitly.
_TOKEN_RE = re.compile(r"[\w\u0900-\u097F]+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff]")
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
# FY mentions are handled by the metadata filter; as search terms they only add noise.
_FY_TERM_RE = re.compile(
    r"(?:\bfy\b\.?|fiscal\s+year|आर्थिक\s+वर्ष|आ\.\s*व\.)?\s*[0-9०-९]{4}\s*[/\-–]\s*[0-9०-९]{2,4}", re.IGNORECASE
)


def strip_fy_terms(text: str) -> str:
    """Remove fiscal-year expressions ("FY 2081/82", "आर्थिक वर्ष २०८२/८३") from a search text."""
    return _FY_TERM_RE.sub(" ", text)


STOPWORDS = frozenset(
    "a an the of and or to in on at by for from with as is are was were be been being it its this that these "
    "those what which who whom when where why how do does did can could should would will shall may might "
    "i me my we our you your he she they them their there here than then so if not no any all about into "
    "please tell explain "
    "को का की ले लाई मा र वा पनि छ छन् हो हुन्छ के कसरी कति कुन किन यो त्यो यस त्यस गर्ने गर्नु भएको".split()
)


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens (stopwords removed) with Devanagari digits normalized."""
    text = to_ascii_digits(_ZERO_WIDTH_RE.sub("", text)).lower()
    tokens = (t.strip("_") for t in _TOKEN_RE.findall(text))
    return [t for t in tokens if t and t not in STOPWORDS]


def is_nepali(text: str) -> bool:
    """True when a meaningful share of the letters are Devanagari."""
    letters = [c for c in text if c.isalpha() or DEVANAGARI_RE.match(c)]
    if not letters:
        return False
    return sum(1 for c in letters if DEVANAGARI_RE.match(c)) / len(letters) > 0.3


def embedding_text(chunk: dict) -> str:
    """Text embedded for vector search: header + context summary + chunk text + hypothetical questions."""
    questions = " ".join(q["question"] for q in chunk.get("hypothetical_questions") or [])
    parts = [strip_fy_terms(chunk["header"]), chunk.get("context_summary") or "", chunk["text"], questions]
    return "\n".join(p for p in parts if p)


# Extra BM25 terms per topic tag, so everyday words ("slab", "deadline", "दर्ता") match legal wording.
TOPIC_TERMS: dict[str, str] = {
    "capital_adequacy": "capital adequacy capital fund punjikosh पुँजीकोष",
    "loan_classification": "loan loss provision classification npl karja noksani कर्जा नोक्सानी वर्गीकरण",
    "credit_limits": "single obligor limit sectoral limit एकल ग्राहक कर्जा सीमा",
    "interest_rates": "interest rate base rate spread byajdar ब्याजदर आधार दर",
    "liquidity": "crr slr cd ratio liquidity अनिवार्य मौज्दात वैधानिक तरलता",
    "governance": "governance board director ceo सुशासन सञ्चालक",
    "kyc_aml": "kyc aml cdd money laundering ग्राहक पहिचान सम्पत्ति शुद्धीकरण",
    "consumer_protection": "customer protection grievance complaint fee गुनासो ग्राहक संरक्षण शुल्क",
    "deposits": "deposit saving fixed deposit निक्षेप बचत मुद्दती",
    "branches": "branch office शाखा",
    "payments": "payment wallet qr card e-money भुक्तानी वालेट",
    "digital_lending": "digital lending online loan विद्युतीय कर्जा",
    "foreign_exchange": "foreign exchange forex dollar lc remittance विदेशी विनिमय सटही प्रतीतपत्र विप्रेषण",
    "cash_limits": "cash limit cash transaction नगद कारोबार सीमा",
    "monetary_policy": "monetary policy policy rate inflation मौद्रिक नीति नीतिगत दर",
    "merger": "merger acquisition मर्जर गाभ्ने",
    "blacklist": "blacklist credit information कालोसूची कर्जा सूचना",
    "penalties": "penalty fine action जरिवाना कारबाही",
}


def bm25_text(chunk: dict) -> str:
    """
    Text indexed by BM25: the embedding text plus every keyword list and references. Titles (the
    directive, point or notice title) are repeated so a question that names the topic matches the
    provision about it rather than any long text that mentions the words in passing.
    """
    titles = [chunk.get("chapter_title") or "", chunk.get("section_title") or ""]
    if chunk.get("doc_type") in ("circular", "notice", "policy", "monetary_policy"):
        titles.append(chunk.get("title") or "")
    extra = (
        titles * 2
        + (chunk.get("keywords_en") or [])
        + (chunk.get("keywords_ne") or [])
        + (chunk.get("keywords_romanized") or [])
        + (chunk.get("keywords_glossary") or [])
        + (chunk.get("amends") or [])
        + (chunk.get("refs") or ([chunk["ref"]] if chunk.get("ref") else []))
        + [TOPIC_TERMS.get(t, t) for t in chunk.get("topics") or []]
        + [chunk.get("plain_explanation") or "", chunk.get("plain_explanation_ne") or ""]
    )
    return embedding_text(chunk) + "\n" + " ".join(extra)
