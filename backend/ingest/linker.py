"""
linker.py
=========
Merge enrichment into the structural chunks and link NRB documents across fiscal years.

1. Every chunk gets its enrichment record (LLM output when the record's content hash still
   matches, otherwise deterministic heuristics).
2. Base chunks get references (``refs``): "bafia s.30" for Act sections, "ud_abc_2082 D.2" and
   "ud_abc_2082 D.2.1" for directive 2 / point 2.1 of a unified-directive edition.
3. Unified-directive editions of the same family form a chain (2080 -> 2081 -> 2082). An edition is
   in force from its own fiscal year until the fiscal year the next edition is issued (both apply in
   that overlap year; the newer one wins). Each point supersedes the same point of the previous edition
   (same reference and a similar title), so answers can say what changed.
4. Circulars amend the edition named in their title ("एकीकृत निर्देशन, २०८२ मा संशोधन"); they apply from
   their own FY until the next edition consolidates them. The directive numbers a circular mentions
   become its ``amends`` and the matching base chunks list it in ``amended_by`` / ``amended_by_ids``.

Runs inside build_index, so adding a document re-links everything without calling any LLM again.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.acts import is_known_key, make_ref
from app.fiscal_year import fy_label, parse_fy
from app.glossary import glossary_terms
from ingest.enrich_with_claude import chunk_hash
from ingest.heuristics import extract_numeric_facts, find_refs, keywords, topic_tags

AMENDING_TYPES = {"circular", "notice"}
_WORD_RE = re.compile(r"[\wऀ-ॿ]+")


def heuristic_enrichment(chunk: dict) -> dict:
    """Offline fallback used when a chunk has no (current) LLM enrichment."""
    title = chunk.get("section_title") or chunk.get("chapter_title") or chunk.get("title")
    label = chunk.get("doc_label") or chunk.get("title")
    questions: list[dict] = []
    if chunk.get("section_title"):
        questions.append({"question": f"What does {label} say about {chunk['section_title']}?", "language": "en"})
        questions.append({"question": f"{chunk['section_title']} सम्बन्धी के व्यवस्था छ?", "language": "ne"})
    text = chunk["header"] + "\n" + chunk["text"]
    return {
        "context_summary": f"Part of {label}" + (f", {title}." if title and title != label else "."),
        "plain_explanation": "",
        "plain_explanation_ne": "",
        "keywords_en": keywords(text),
        "keywords_ne": [],
        "keywords_romanized": [],
        "hypothetical_questions": questions,
        "amends": [],
        "amended_by": [],
        "effective_from_fy": None,
        "effective_to_fy": None,
        "topics": topic_tags(text),
        "numeric_facts": extract_numeric_facts(chunk["text"], chunk.get("fiscal_year")),
    }


def own_refs(chunk: dict) -> list[str]:
    """References that name this chunk: an Act section, or a directive / directive point / annex."""
    key, no = chunk.get("act_key"), chunk.get("section_no")
    if not key or not is_known_key(key) or not no:
        return []
    if chunk["doc_type"] == "act" and chunk.get("unit_kind") == "section":
        return [make_ref(key, no, "s")]
    if chunk["doc_type"] == "directive":
        if chunk.get("unit_kind") == "schedule":
            return [make_ref(key, no, "Sch")] if re.fullmatch(r"\d+(?:\.\d+)?", no) else []
        d, _, p = no.partition(".")
        refs = [make_ref(key, d, "D")]
        if p and p != "0":
            refs.append(make_ref(key, f"{d}.{p}", "D"))
        return refs
    return []


def merge(structural: dict, records: dict[str, dict]) -> list[dict]:
    """Attach enrichment to every chunk (LLM record when current, else heuristic)."""
    merged: list[dict] = []
    for chunk in structural["chunks"]:
        rec = records.get(chunk["chunk_id"])
        current = rec is not None and rec.get("chunk_hash") == chunk_hash(chunk)
        data = rec["data"] if current else heuristic_enrichment(chunk)
        c = dict(chunk)
        c["enrichment_source"] = rec["source"] if current else "heuristic"
        for key in ("context_summary", "plain_explanation", "plain_explanation_ne", "keywords_en", "keywords_ne",
                    "keywords_romanized", "hypothetical_questions", "numeric_facts"):
            c[key] = data.get(key) or ([] if key.startswith(("keywords", "hypo", "numeric")) else "")
        c["topics"] = sorted(set(data.get("topics") or []) | set(topic_tags(chunk["header"] + " " + chunk["text"])))
        c["keywords_glossary"] = glossary_terms(chunk["header"] + "\n" + chunk["text"])
        c["refs"] = own_refs(c)
        c["ref"] = c["refs"][-1] if c["refs"] else None
        default_key = chunk.get("amends_key") or (chunk.get("act_key") if chunk["doc_type"] == "directive" else None)
        c["mentions"] = [r for r in find_refs(chunk["text"], default_key) if r not in c["refs"]]
        if chunk["doc_type"] in AMENDING_TYPES and chunk.get("amends_key"):
            family_prefix = chunk["amends_key"].rsplit("_", 1)[0] + "_"
            c["amends"] = sorted({r for r in c["mentions"] if r.startswith(family_prefix)}
                                 | set(data.get("amends") or []))
        else:
            c["amends"] = []
        c["stated_amended_by"] = data.get("amended_by") or []
        merged.append(c)
    return merged


def _similar(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return a == b
    wa, wb = set(_WORD_RE.findall(a)), set(_WORD_RE.findall(b))
    return bool(wa and wb) and len(wa & wb) / max(len(wa), len(wb)) >= 0.5


def link(chunks: list[dict], documents: list[dict] | None = None) -> list[dict]:
    """Compute effective ranges, edition supersession and circular amendment links (mutates and returns chunks)."""
    for c in chunks:
        c["amended_by"], c["amended_by_ids"], c["supersedes"], c["superseded_by"] = [], [], [], []

    # ── editions: family -> sorted [(year, fy_start, doc_key)] ────────────────
    editions: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    seen: set[str] = set()
    for c in chunks:
        key = c.get("act_key")
        if c["doc_type"] == "directive" and key and key not in seen and parse_fy(c.get("effective_from_fy")):
            seen.add(key)
            family, year = key.split("_")[1], int(key.rsplit("_", 1)[1])
            editions[family].append((year, parse_fy(c["effective_from_fy"]), key))  # type: ignore[arg-type]
    next_edition_fy: dict[str, int | None] = {}   # doc_key -> FY start of the next edition
    previous_edition: dict[str, str] = {}
    for eds in editions.values():
        eds.sort()
        for i, (_y, _fy, key) in enumerate(eds):
            next_edition_fy[key] = eds[i + 1][1] if i + 1 < len(eds) else None
            if i:
                previous_edition[key] = eds[i - 1][2]

    def edition_end(key: str | None) -> str | None:
        nxt = next_edition_fy.get(key or "")
        return fy_label(nxt) if nxt else None

    by_ref: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        for r in c["refs"]:
            by_ref[r].append(c)

    # ── effective ranges ──────────────────────────────────────────────────────
    for c in chunks:
        if c["doc_type"] == "directive":
            c["effective_to_fy"] = edition_end(c.get("act_key"))
        elif c["doc_type"] in AMENDING_TYPES:
            key = c.get("amends_key")
            # amends an edition we have: valid until the next edition; amends an older, missing edition
            # whose successor we have (e.g. FX circulars of 2082/83 that amend the 2081 edition): same rule
            end = edition_end(key) if key in next_edition_fy else None
            if key and key not in next_edition_fy:
                family, year = key.split("_")[1], int(key.rsplit("_", 1)[1])
                later = [fy for y, fy, _k in editions.get(family, []) if y > year]
                end = fy_label(min(later)) if later else None
            c["effective_to_fy"] = end
        elif c["doc_type"] == "monetary_policy":
            c["effective_to_fy"] = c.get("effective_from_fy")
        else:
            c["effective_from_fy"], c["effective_to_fy"] = None, None

    # ── edition supersession (same point in the previous edition) ─────────────
    for c in chunks:
        prev_key = previous_edition.get(c.get("act_key") or "")
        if not prev_key or not c["refs"]:
            continue
        ref = c["refs"][-1]
        old_ref = ref.replace(c["act_key"], prev_key, 1)
        for old in by_ref.get(old_ref, []):
            if _similar(old.get("section_title"), c.get("section_title")) and old.get("sub") == c.get("sub"):
                c["supersedes"].append(old["chunk_id"])
                old["superseded_by"].append(c["chunk_id"])

    # ── circular amendments ───────────────────────────────────────────────────
    for a in chunks:
        if a["doc_type"] not in AMENDING_TYPES:
            continue
        label = a["title"][:90] + (f" (FY {a['effective_from_fy']})" if a.get("effective_from_fy") else "")
        # "निर्देशन नं. १४ को बुँदा २" names a point: link that point, not every point of directive 14
        named_points = {r.rsplit(".", 1)[0] for r in a["amends"] if r.count(".") == 2}
        for ref in a["amends"]:
            if ref in named_points:
                continue
            for base in by_ref.get(ref, []):
                if a["chunk_id"] not in base["amended_by_ids"]:
                    base["amended_by_ids"].append(a["chunk_id"])
                if label not in base["amended_by"]:
                    base["amended_by"].append(label)

    for c in chunks:
        c["amended_by"] = sorted(set(c["amended_by"]) | set(c.pop("stated_amended_by", [])))
        c["supersedes"] = sorted(set(c["supersedes"]) - {c["chunk_id"]})
        c["superseded_by"] = sorted(set(c["superseded_by"]) - {c["chunk_id"]})
    return chunks
