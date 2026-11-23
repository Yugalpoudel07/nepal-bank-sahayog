"""
store.py
========
Read-only access to the built index: final chunks, parents, Chroma vectors and BM25.

Both retrievers apply the same fiscal-year validity filter:
    effective_from_fy <= FY  and  (effective_to_fy is null or effective_to_fy >= FY)
Acts, rules and policies have no effective range, so they are always eligible;
whether a newer document overrides them is decided in `retrieval.py`.
"""

from __future__ import annotations

import json
import logging
import pickle
from collections import defaultdict
from pathlib import Path

from app.config import Settings
from app.embeddings import Embeddings, load_embeddings
from app.fiscal_year import fy_label, parse_fy
from app.textproc import tokenize

log = logging.getLogger(__name__)

COLLECTION = "nrb_chunks"


def chroma_client(path: Path):
    """Persistent Chroma client with telemetry disabled."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))


def is_valid_for(chunk: dict, fy: int) -> bool:
    """True when the chunk is in force in fiscal year `fy` (start year)."""
    start = parse_fy(chunk.get("effective_from_fy"))
    end = parse_fy(chunk.get("effective_to_fy"))
    return (start is None or start <= fy) and (end is None or end >= fy)


class IndexStore:
    """Loads data/index/* once and serves lookups and searches."""

    def __init__(self, settings: Settings, embeddings: Embeddings | None = None) -> None:
        index_dir = settings.index_dir
        meta_path = index_dir / "index_meta.json"
        if not meta_path.exists():
            raise FileNotFoundError("Index not found. Run: python -m ingest.run_all")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        payload = json.loads((index_dir / "chunks.json").read_text(encoding="utf-8"))
        self.documents: list[dict] = payload["documents"]
        self.parents: dict[str, dict] = payload["parents"]
        self.chunks: dict[str, dict] = {c["chunk_id"]: c for c in payload["chunks"]}

        with (index_dir / "bm25.pkl").open("rb") as fh:
            bm = pickle.load(fh)  # noqa: S301 - file produced locally by build_index
        if bm.get("content_hash") != self.meta.get("content_hash"):
            raise RuntimeError("bm25.pkl and index_meta.json disagree; rebuild with python -m ingest.build_index --force")
        self._bm25 = bm["bm25"]
        self._bm25_ids: list[str] = bm["ids"]

        self._collection = chroma_client(index_dir / "chroma").get_collection(COLLECTION)
        self._embeddings = embeddings
        self._settings = settings

        self.by_ref: dict[str, list[str]] = defaultdict(list)
        self.amenders: dict[str, list[str]] = defaultdict(list)
        self.mentioners: dict[str, list[str]] = defaultdict(list)
        for cid, c in self.chunks.items():
            for r in c.get("refs") or ([c["ref"]] if c.get("ref") else []):
                self.by_ref[r].append(cid)
            for r in c.get("amends") or []:
                self.amenders[r].append(cid)
            for r in c.get("mentions") or []:
                self.mentioners[r].append(cid)
        # directive editions per family with their validity range: {"abc": [(doc_key, from_fy, to_fy), ...]}
        self.editions: dict[str, list[tuple[str, int | None, int | None]]] = defaultdict(list)
        seen: set[str] = set()
        for c in self.chunks.values():
            key = c.get("act_key")
            if c["doc_type"] == "directive" and key and key not in seen:
                seen.add(key)
                self.editions[c.get("family") or ""].append(
                    (key, parse_fy(c.get("effective_from_fy")), parse_fy(c.get("effective_to_fy"))))
        log.info("index loaded", extra={"kv": {"chunks": len(self.chunks), "model": self.meta.get("embedding_model")}})

    # ── embeddings (lazy: model load is slow) ────────────────────────────────
    @property
    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            model = self.meta.get("embedding_model") or self._settings.embedding_model
            if model != self._settings.embedding_model:
                log.warning("EMBEDDING_MODEL differs from the model the index was built with; using the index's model",
                            extra={"kv": {"index_model": model}})
            self._embeddings = load_embeddings(model, self._settings.embedding_batch_size)
        return self._embeddings

    # ── searches ─────────────────────────────────────────────────────────────
    def vector_search(self, query: str, fy: int, k: int) -> list[tuple[str, float]]:
        """Top-k chunk ids by cosine similarity among chunks valid in `fy`."""
        res = self._collection.query(
            query_embeddings=[self.embeddings.embed_query(query)],
            n_results=min(k, len(self.chunks)),
            where={"$and": [{"eff_from": {"$lte": fy}}, {"eff_to": {"$gte": fy}}]},
        )
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return [(i, 1.0 - d) for i, d in zip(ids, dists)]

    def bm25_search(self, query: str, fy: int, k: int) -> list[tuple[str, float]]:
        """Top-k chunk ids by BM25 among chunks valid in `fy`."""
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            ((cid, float(s)) for cid, s in zip(self._bm25_ids, scores)
             if s > 0 and is_valid_for(self.chunks[cid], fy)),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:k]

    # ── metadata helpers ─────────────────────────────────────────────────────
    def parent_text(self, chunk: dict, max_chars: int) -> str:
        """Small-to-big: the full parent section when it is not too long, else the chunk itself."""
        parent = self.parents.get(chunk.get("parent_id", ""))
        if parent and chunk["chunk_id"] != chunk["parent_id"] and len(parent["text"]) <= max_chars:
            return parent["text"]
        return chunk["text"]

    def editions_for(self, fy: int, family: str | None = None) -> list[str]:
        """Directive-edition keys in force in `fy` (newest first), optionally for one family."""
        out = []
        for fam, eds in self.editions.items():
            if family and fam != family:
                continue
            out += [(k, start or 0) for k, start, end in eds
                    if (start is None or start <= fy) and (end is None or end >= fy)]
        return [k for k, _ in sorted(out, key=lambda x: x[1], reverse=True)]

    def available_fys(self) -> list[str]:
        """Fiscal years for which at least one document exists (newest first)."""
        years = {parse_fy(d.get("effective_fy")) for d in self.documents} - {None}
        return [fy_label(y) for y in sorted(years, reverse=True)]  # type: ignore[arg-type]

    def document_list(self) -> list[dict]:
        """Indexed documents with their FY and chunk counts."""
        counts: dict[str, int] = defaultdict(int)
        for c in self.chunks.values():
            counts[c["doc_id"]] += 1
        return [
            {
                "doc_id": d["doc_id"], "title": d["title"], "doc_label": d.get("doc_label"), "family": d.get("family"),
                "doc_type": d["doc_type"], "language": d["language"],
                "fiscal_year": d.get("effective_fy"), "issue_date_bs": d.get("issue_date_bs"),
                "source_url": d.get("source_url"), "path": d["path"], "chunks": counts.get(d["doc_id"], 0),
            }
            for d in self.documents
        ]
