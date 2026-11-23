"""
retrieval.py
============
Fiscal-year-aware hybrid retrieval over NRB documents.

Pipeline for one question and one fiscal year:
  1. Direct lookup — provisions named in the question ("निर्देशन नं. २", "directive 2 point 1",
     "BAFIA section 30") are fetched by metadata from the directive editions in force in that FY,
     together with the circulars that amend them.
  2. Hybrid search — BM25 + vector (both FY-filtered), fused with weighted reciprocal-rank fusion.
     Chunks of the rulebook family the question is about (payment systems, microfinance, ...; class
     A/B/C banks by default) are preferred over the parallel rules for other institution types.
  3. Edition overrides — in the overlap year where an old and a new unified-directive edition are both
     in force, the old point is marked "overridden" and the new one is pulled in; current points bring
     their previous-edition version along ("previous_version") so the answer can say what changed.
  4. Amendments — circulars valid in the FY that amend a selected point are added ("amendment").
  5. Dedupe by parent unit (small-to-big), optional cross-encoder rerank.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.acts import ACTS, detect_family, make_ref
from app.config import Settings
from app.glossary import glossary_terms
from app.section_detect import SectionRef
from app.store import IndexStore, is_valid_for
from app.textproc import strip_fy_terms

log = logging.getLogger(__name__)

DIRECT_BOOST = 1.0
_RATE_INTENT_RE = re.compile(r"\brates?\b|ratio|limit|percent|%|how\s+much|दर|अनुपात|सीमा|प्रतिशत|कति", re.IGNORECASE)
MAX_HISTORY_ITEMS = 3
MAX_AMENDMENTS = 3
FAMILY_BOOST = 1.3     # chunks of the family the question is about
OTHER_FAMILY = 0.75    # chunks of a different institution type's rulebook
ANNEX_WEIGHT = 0.6     # directive annexes/reporting forms: long and keyword-dense, rarely the answer itself
MAX_PIECES_PER_PARENT = 2  # a provision too long to show whole may contribute several of its pieces
LEAD_PIECE_MIN_CHARS = 200  # the first piece at least this long carries a long provision's main rule


@dataclass
class Hit:
    """A chunk selected for the LLM context."""

    chunk: dict
    score: float
    status: str = "current"  # current | overridden | previous_version | amendment
    reasons: set[str] = field(default_factory=set)

    @property
    def id(self) -> str:
        return self.chunk["chunk_id"]


class Reranker:
    """Optional multilingual cross-encoder (ENABLE_RERANK=true)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self._model.predict([(query, t) for t in texts])]


class Retriever:
    """Combines direct lookup, hybrid search and FY override logic."""

    def __init__(self, store: IndexStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._reranker: Reranker | None = None
        if settings.enable_rerank:
            try:
                self._reranker = Reranker(settings.rerank_model)
            except Exception as exc:  # optional component
                log.warning("reranker disabled", extra={"kv": {"error": str(exc)}})

    # ── step 1 ────────────────────────────────────────────────────────────────
    def _refs_for(self, sref: SectionRef, fy: int, family: str | None) -> list[str]:
        if sref.kind == "D":
            fam = sref.act_key or family
            keys = self.store.editions_for(fy, fam) or self.store.editions_for(fy)
            return [make_ref(k, sref.number, "D") for k in keys]
        keys = [sref.act_key] if sref.act_key in ACTS else list(ACTS)
        return [make_ref(k, sref.number, sref.kind) for k in keys]

    def direct_lookup(self, refs: list[SectionRef], fy: int, family: str | None = None) -> dict[str, Hit]:
        hits: dict[str, Hit] = {}
        for sref in refs:
            for ref in self._refs_for(sref, fy, family):
                ids = (self.store.by_ref.get(ref, []) + self.store.amenders.get(ref, [])
                       + self.store.mentioners.get(ref, []))
                for cid in ids:
                    c = self.store.chunks[cid]
                    if is_valid_for(c, fy) and cid not in hits:
                        hits[cid] = Hit(c, DIRECT_BOOST + 0.1 * (c["doc_type"] in ("circular", "notice")),
                                        reasons={f"direct:{ref}"})
        return hits

    # ── step 2 ────────────────────────────────────────────────────────────────
    def hybrid(self, query: str, fy: int) -> dict[str, Hit]:
        n = self.settings.candidates_per_retriever
        k = self.settings.rrf_k
        fused: dict[str, float] = {}
        # bilingual query expansion: "blacklisted" also searches "कालोसूची" and "kalo suchi"
        expanded = " ".join([query, *glossary_terms(query)])
        bm25 = self.store.bm25_search(expanded, fy, n)
        rankings = [
            (self.settings.bm25_weight, bm25),
            (self.settings.vector_weight, self.store.vector_search(query, fy, n)),
        ]
        if _RATE_INTENT_RE.search(query):
            # Rate/slab questions are answered by rate tables and rate statements: rank the chunks that
            # carry numbers (tables or extracted numeric facts) as a third, equally weighted signal.
            numeric = [(cid, sc) for cid, sc in bm25
                       if self.store.chunks[cid].get("is_table") or self.store.chunks[cid].get("numeric_facts")]
            rankings.append((max(self.settings.bm25_weight, self.settings.vector_weight), numeric))
        for weight, results in rankings:
            for rank, (cid, _score) in enumerate(results):
                fused[cid] = fused.get(cid, 0.0) + weight / (k + rank + 1)
        hits = {cid: Hit(self.store.chunks[cid], s, reasons={"hybrid"}) for cid, s in fused.items()}
        for h in hits.values():
            if h.chunk["doc_type"] == "directive" and h.chunk.get("unit_kind") == "schedule":
                h.score *= ANNEX_WEIGHT
        return hits

    def _weight_families(self, hits: dict[str, Hit], family: str) -> None:
        """Prefer the rulebook of the institution type the question is about."""
        for h in hits.values():
            fam = h.chunk.get("family") or "general"
            if fam == family:
                h.score *= FAMILY_BOOST
            elif fam != "general":
                h.score *= OTHER_FAMILY

    # ── step 3 ────────────────────────────────────────────────────────────────
    def _resolve_overrides(self, hits: dict[str, Hit], fy: int) -> None:
        for hit in list(hits.values()):
            replacements = [
                self.store.chunks[s] for s in hit.chunk.get("superseded_by", [])
                if s in self.store.chunks and is_valid_for(self.store.chunks[s], fy)
            ]
            if not replacements:
                continue
            hit.status = "overridden"
            for r in replacements:
                existing = hits.get(r["chunk_id"])
                if existing is None:
                    hits[r["chunk_id"]] = Hit(r, hit.score + 1e-6, reasons={f"overrides:{hit.id}"})
                else:
                    existing.score = max(existing.score, hit.score + 1e-6)

    def _history(self, selected: list[Hit], fy: int, exclude: set[str]) -> list[Hit]:
        """Previous versions of current amending chunks (valid before `fy`), for change explanations."""
        out: list[Hit] = []
        for hit in selected:
            if hit.status != "current":
                continue
            for pid in hit.chunk.get("supersedes", []):
                prev = self.store.chunks.get(pid)
                if prev is None or pid in exclude or is_valid_for(prev, fy):
                    continue
                exclude.add(pid)
                out.append(Hit(prev, hit.score, status="previous_version", reasons={f"previous_of:{hit.id}"}))
                if len(out) >= MAX_HISTORY_ITEMS:
                    return out
                break
        return out

    def _amendments(self, selected: list[Hit], fy: int, exclude: set[str]) -> list[Hit]:
        """Circulars valid in `fy` that amend the selected provisions (newest first)."""
        out: list[Hit] = []
        for hit in selected:
            ids = [i for i in hit.chunk.get("amended_by_ids") or [] if i in self.store.chunks and i not in exclude]
            cands = [self.store.chunks[i] for i in ids if is_valid_for(self.store.chunks[i], fy)]
            for c in sorted(cands, key=lambda c: (c.get("published") or "", c["chunk_id"]), reverse=True):
                exclude.add(c["chunk_id"])
                out.append(Hit(c, hit.score, status="amendment", reasons={f"amends:{hit.id}"}))
                if len(out) >= MAX_AMENDMENTS:
                    return out
        return out

    # ── step 5 ────────────────────────────────────────────────────────────────
    def _dedupe(self, hits: dict[str, Hit]) -> list[Hit]:
        """
        Small-to-big dedupe. A parent short enough to be shown whole keeps only its best piece (the
        context shows the whole parent anyway). A parent too long to show whole (e.g. a 60,000-character
        directive point) keeps up to MAX_PIECES_PER_PARENT pieces, because the context then shows only the
        pieces themselves and the best-scoring piece is often not the one with the answer.
        """
        kept: dict[str, int] = {}
        out: list[Hit] = []
        for h in sorted(hits.values(), key=lambda h: h.score, reverse=True):
            pid = h.chunk["parent_id"]
            parent = self.store.parents.get(pid)
            shown_whole = parent is None or len(parent["text"]) <= self.settings.max_section_chars
            limit = 1 if shown_whole else MAX_PIECES_PER_PARENT
            if kept.get(pid, 0) < limit:
                kept[pid] = kept.get(pid, 0) + 1
                out.append(h)
        return out

    def _lead_piece(self, parent_id: str) -> dict | None:
        """Opening piece of a split provision (its first substantial chunk), where NRB states the main rule."""
        if not hasattr(self, "_pieces"):
            self._pieces: dict[str, list[dict]] = {}
            for c in self.store.chunks.values():
                self._pieces.setdefault(c["parent_id"], []).append(c)
            for pieces in self._pieces.values():
                pieces.sort(key=lambda c: int(c["chunk_id"].rpartition("#")[2]) if "#" in c["chunk_id"] else 0)
        for c in self._pieces.get(parent_id, []):
            if len(c["text"]) >= LEAD_PIECE_MIN_CHARS:
                return c
        return None

    def _with_lead_pieces(self, selected: list[Hit]) -> list[Hit]:
        """
        When a later piece of a provision too long to show whole was selected (e.g. the exceptions of
        "Directive 2, point 9"), add the provision's opening piece right after it, because the opening piece
        holds the main rule (for point 2.9: the provisioning rate per loan category).
        """
        out: list[Hit] = []
        ids = {h.id for h in selected}
        for h in selected:
            out.append(h)
            parent = self.store.parents.get(h.chunk["parent_id"])
            if parent is None or len(parent["text"]) <= self.settings.max_section_chars:
                continue
            lead = self._lead_piece(h.chunk["parent_id"])
            if lead is not None and lead["chunk_id"] not in ids:
                ids.add(lead["chunk_id"])
                out.append(Hit(lead, h.score, reasons={f"lead_of:{h.id}"}))
        return out

    # ── public ────────────────────────────────────────────────────────────────
    def retrieve(self, query: str, fy: int, section_refs: list[SectionRef]) -> list[Hit]:
        """Return context hits: current ones first (top_k), then amendments, overridden and previous versions."""
        family = detect_family(query) or self.settings.default_family
        hits = self.hybrid(strip_fy_terms(query), fy)
        if family:
            self._weight_families(hits, family)
        for cid, h in self.direct_lookup(section_refs, fy, detect_family(query)).items():
            if cid in hits:
                hits[cid].score += h.score
                hits[cid].reasons |= h.reasons
            else:
                hits[cid] = h
        self._resolve_overrides(hits, fy)

        ranked = self._dedupe(hits)

        current = [h for h in ranked if h.status == "current"]
        if self._reranker and current:
            pool = current[: self.settings.top_k * 3]
            scores = self._reranker.scores(query, [h.chunk["header"] + "\n" + h.chunk["text"] for h in pool])
            direct = [h for h in pool if any(r.startswith("direct:") for r in h.reasons)]
            rest = [h for _, h in sorted(zip(scores, pool), key=lambda x: x[0], reverse=True) if h not in direct]
            current = direct + rest
        selected = self._with_lead_pieces(current[: self.settings.top_k])
        selected_ids = {h.id for h in selected}

        overridden = [
            h for h in ranked
            if h.status == "overridden" and any(s in selected_ids for s in h.chunk.get("superseded_by", []))
        ][:MAX_HISTORY_ITEMS]
        exclude = selected_ids | {h.id for h in overridden}
        amendments = self._amendments(selected, fy, exclude)
        history = self._history(selected, fy, exclude)
        return selected + amendments + overridden + history
