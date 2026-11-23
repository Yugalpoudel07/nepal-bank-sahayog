"""
prompts.py
==========
System prompt, context formatting and the few fixed, localized sentences the
engine adds itself (FY line, disclaimer, warnings, not-found message) for Nepal Bank Sahayak.
"""

from __future__ import annotations

from app.acts import ref_label
from app.retrieval import Hit

INSTITUTION_LABELS = {
    "A": "class A commercial banks", "B": "class B development banks", "C": "class C finance companies",
    "D": "class D microfinance", "infra": "infrastructure development bank", "psp": "payment service providers/operators",
    "fx": "foreign-exchange licensees", "coop": "limited-banking cooperative banks", "all": "all",
}

SYSTEM_PROMPT = """You are "Nepal Bank Sahayak", an assistant that explains Nepal Rastra Bank (NRB) rules —
banking Acts, NRB unified directives, circulars, notices and monetary policy — in plain language to the public
(bank customers, small businesses, students) and to people working in banks.

Rules you must follow:
1. Answer ONLY from the CONTEXT blocks. If the context does not contain the answer, say clearly (in the reply
   language) that it was not found in the provided documents.
2. Never invent or estimate rates, ratios, limits, amounts, dates or deadlines. Quote numbers exactly as written.
3. Cite sources inline right after the statement they support, using the "Cite as" label of the block,
   e.g. [Unified Directive (class A/B/C banks) 2082, Directive 2, Point 9].
4. The answer applies to fiscal year {fy}. Do NOT write a fiscal-year heading line or a disclaimer: the app adds
   both itself.
5. Blocks marked CURRENT are in force for FY {fy}. A block marked AMENDMENT is a circular that changed a
   current provision: its rule takes precedence over the provision text, so state the amended rule and cite
   the circular. Blocks marked OVERRIDDEN or PREVIOUS VERSION are older editions: use them only to explain
   what changed ("Under the 2081 directive it was X; the 2082 directive makes it Y").
6. NRB rules differ by institution type: class A (commercial banks), B (development banks), C (finance
   companies), D (microfinance), the infrastructure development bank, payment service providers/operators,
   foreign-exchange licensees and cooperative banks. Say which type a rule applies to. If the question does
   not say and the types differ, give the rule for commercial banks (class A) and mention that it differs for others.
7. Never do arithmetic yourself (no totals, products or percentages of the user's amounts). When a calculator
   result is provided, present its breakdown as given. If the calculator returned an error or no calculator
   was run, say the calculation is not available, and give only the rule from the documents.
8. {language_rule}
9. Explain in plain words first (what it means for a customer or a bank), then the exact rule. Be concise:
   short paragraphs, bullet points, markdown tables for rates and limits.
10. Write numbers with ASCII digits (0-9) in English replies and Devanagari digits (०-९) in Nepali replies;
   never use digits of any other script."""

LANGUAGE_RULES = {
    "en": "Reply in English.",
    "ne": "Reply in Nepali (Devanagari script), even if the question was typed in romanized Nepali. "
          "Keep Act names, directive numbers and figures exact.",
}

REWRITE_PROMPT = """Rewrite the user's latest message as one standalone question about Nepal banking rules, using
the conversation for missing context (topic, institution type, directive, fiscal year). Keep the user's language.
Output only the question.

Conversation:
{history}

Latest message: {question}
Standalone question:"""

TEXTS = {
    "en": {
        "applies": "**Applies to FY {fy}**",
        "disclaimer": "_General information from NRB documents, not legal or financial advice — check the latest "
                      "NRB circulars or ask your bank._",
        "not_found": "I could not find this in the provided NRB documents for FY {fy}. Try rephrasing, name the "
                     "directive or Act, or choose another fiscal year.",
        "fy_missing": "No documents for FY {requested} are indexed. This answer uses FY {used}, the latest "
                      "available year — the rules for FY {requested} may differ.",
        "fy_no_docs": "No directive or circular for this year is indexed yet, so this answer uses only the Acts.",
        "calc_failed": "The calculation is not available: {error}",
    },
    "ne": {
        "applies": "**लागू हुने आर्थिक वर्ष: {fy}**",
        "disclaimer": "_यो NRB कागजातमा आधारित सामान्य जानकारी हो, कानुनी वा वित्तीय सल्लाह होइन — पछिल्ला परिपत्र "
                      "हेर्नुहोस् वा आफ्नो बैंकसँग बुझ्नुहोस्।_",
        "not_found": "आर्थिक वर्ष {fy} का लागि उपलब्ध NRB कागजातहरूमा यो विषय भेटिएन। प्रश्न फरक तरिकाले "
                     "सोध्नुहोस्, निर्देशन वा ऐन उल्लेख गर्नुहोस्, वा अर्को आर्थिक वर्ष छान्नुहोस्।",
        "fy_missing": "आर्थिक वर्ष {requested} का कागजात उपलब्ध छैनन्। यो उत्तर उपलब्ध पछिल्लो आर्थिक वर्ष {used} "
                      "अनुसार हो — {requested} का नियम फरक हुन सक्छन्।",
        "fy_no_docs": "यस वर्षको कुनै निर्देशन वा परिपत्र अझै समावेश छैन, त्यसैले यो उत्तर ऐनमा मात्र आधारित छ।",
        "calc_failed": "हिसाब गर्न सकिएन: {error}",
    },
}

STATUS_LABELS = {
    "current": "CURRENT for FY {fy}",
    "overridden": "OVERRIDDEN in FY {fy} by a newer directive edition in this context (history only)",
    "previous_version": "PREVIOUS VERSION (applied {range}; history only)",
    "amendment": "AMENDMENT — circular in force for FY {fy} that changes a provision in this context",
}


def cite_label(chunk: dict) -> str:
    """Inline citation label, e.g. "Unified Directive (class A/B/C banks) 2082, Directive 2, Point 2.9"."""
    parts = [chunk.get("doc_label") or chunk["title"]]
    no, kind = chunk.get("section_no"), chunk.get("unit_kind")
    if chunk["doc_type"] == "directive" and kind == "section" and no:
        d, _, p = no.partition(".")
        parts.append(f"Directive {d}" + (f", Point {no}" if p and p != "0" else ""))
    elif kind == "schedule" and no:
        parts.append(f"Annex {no}" if chunk["doc_type"] == "directive" else f"Schedule-{no}")
    elif no:
        parts.append(f"{'r' if chunk['doc_type'] == 'rules' else 's'}.{no}")
    elif chunk.get("section_title") and kind == "section":
        parts.append(chunk["section_title"])  # number lost in OCR: cite by title
    fy = chunk.get("effective_from_fy") or chunk.get("fiscal_year")
    if fy:
        parts.append(f"FY {fy}")
    return ", ".join(parts)


def _range(chunk: dict) -> str:
    start, end = chunk.get("effective_from_fy"), chunk.get("effective_to_fy")
    if start and end and start != end:
        return f"FY {start} to {end}"
    if start:
        return f"FY {start}"
    return "before the amendment"


def format_context(hits: list[Hit], fy: str, texts: dict[str, str]) -> str:
    """Render the context blocks given to the LLM. `texts` maps chunk_id -> (parent) text to show."""
    blocks = []
    for i, h in enumerate(hits, 1):
        c = h.chunk
        status = STATUS_LABELS[h.status].format(fy=fy, range=_range(c))
        lines = [f"[S{i}] {c['header']}", f"Status: {status}", f"Cite as: [{cite_label(c)}]"]
        if c.get("institutions"):
            lines.append("Applies to: " + ", ".join(INSTITUTION_LABELS.get(i, i) for i in c["institutions"]))
        if c.get("amends"):
            lines.append("Amends: " + ", ".join(ref_label(r) for r in c["amends"]))
        if c.get("amended_by"):
            lines.append("Amended by: " + "; ".join(c["amended_by"][:4]))
        lines.append(texts[c["chunk_id"]])
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)
