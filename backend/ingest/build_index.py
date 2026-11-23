"""
build_index.py
==============
Step 4: build the Chroma vector store and the pickled BM25 index from the SAME
enriched + linked chunks, and record a content hash.

* Embedding text = header + context_summary + chunk text + hypothetical questions.
* BM25 text      = the same, plus all keyword lists and references.
* data/index/
    chunks.json       final chunks (metadata the runtime uses) + parents + documents
    chroma/           persistent Chroma collection
    bm25.pkl          pickled BM25Okapi + chunk id order
    index_meta.json   content hash, embedding model, counts

The index is rebuilt only when the content hash changes (new/edited chunks,
new enrichment, different embedding model) or with --force. The API server
calls `ensure_index()` at start-up, so a stale index is rebuilt automatically.

Usage (from backend/):
    python -m ingest.build_index [--force]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import pickle

from app.config import Settings, get_settings
from app.embeddings import load_embeddings
from app.fiscal_year import parse_fy
from app.logging_setup import setup_logging
from app.store import COLLECTION, chroma_client
from app.textproc import bm25_text, embedding_text, tokenize
from ingest.enrich_with_claude import load_records
from ingest.linker import link, merge

log = logging.getLogger("build_index")

INDEX_VERSION = 4
NO_FY_FROM = 0
NO_FY_TO = 9999


def final_payload(settings: Settings) -> dict:
    """Structural chunks + enrichment, merged and linked."""
    structural = json.loads(settings.structural_chunks_path.read_text(encoding="utf-8"))
    records = load_records(settings.enriched_chunks_path)
    chunks = link(merge(structural, records))
    return {"documents": structural["documents"], "parents": structural["parents"], "chunks": chunks}


def content_hash(payload: dict, embedding_model: str) -> str:
    """Hash of everything that affects the index: chunks, the indexed texts, model and format version."""
    h = hashlib.sha256()
    h.update(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for c in payload["chunks"]:
        h.update(embedding_text(c).encode("utf-8"))
        h.update(" ".join(tokenize(bm25_text(c))).encode("utf-8"))
    h.update(f"{embedding_model}|{INDEX_VERSION}".encode())
    return h.hexdigest()


def chroma_metadata(c: dict) -> dict:
    """Flat metadata for Chroma `where` filters (no None values allowed)."""
    return {
        "doc_type": c["doc_type"],
        "act_key": c.get("act_key") or "",
        "family": c.get("family") or "general",
        "language": c.get("language") or "",
        "eff_from": parse_fy(c.get("effective_from_fy")) or NO_FY_FROM,
        "eff_to": parse_fy(c.get("effective_to_fy")) or NO_FY_TO,
    }


def index_is_current(settings: Settings, digest: str) -> bool:
    meta_path = settings.index_dir / "index_meta.json"
    if not meta_path.exists():
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return (
        meta.get("content_hash") == digest
        and (settings.index_dir / "bm25.pkl").exists()
        and (settings.index_dir / "chunks.json").exists()
        and (settings.index_dir / "chroma").exists()
    )


def build(settings: Settings, force: bool = False) -> dict:
    """Build (or skip when current) and return index_meta."""
    from rank_bm25 import BM25Okapi

    payload = final_payload(settings)
    digest = content_hash(payload, settings.embedding_model)
    meta_path = settings.index_dir / "index_meta.json"
    if not force and index_is_current(settings, digest):
        log.info("index is up to date", extra={"kv": {"hash": digest[:12]}})
        return json.loads(meta_path.read_text(encoding="utf-8"))

    chunks = payload["chunks"]
    if not chunks:
        raise SystemExit("No chunks to index. Add documents to data/raw/ and run the pipeline.")
    settings.index_dir.mkdir(parents=True, exist_ok=True)
    meta_path.unlink(missing_ok=True)  # mark incomplete until the end

    log.info("embedding chunks", extra={"kv": {"n": len(chunks), "model": settings.embedding_model}})
    emb = load_embeddings(settings.embedding_model, settings.embedding_batch_size)
    vectors = emb.embed_documents([embedding_text(c) for c in chunks])

    client = chroma_client(settings.index_dir / "chroma")
    try:
        client.delete_collection(COLLECTION)
    except Exception:  # collection does not exist yet
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    step = 500
    for i in range(0, len(chunks), step):
        part = chunks[i: i + step]
        col.add(
            ids=[c["chunk_id"] for c in part],
            embeddings=vectors[i: i + step],
            metadatas=[chroma_metadata(c) for c in part],
        )

    bm25 = BM25Okapi([tokenize(bm25_text(c)) for c in chunks])
    with (settings.index_dir / "bm25.pkl").open("wb") as fh:
        pickle.dump({"index_version": INDEX_VERSION, "ids": [c["chunk_id"] for c in chunks], "bm25": bm25,
                     "content_hash": digest}, fh)

    (settings.index_dir / "chunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "content_hash": digest,
        "index_version": INDEX_VERSION,
        "embedding_model": settings.embedding_model,
        "chunks": len(chunks),
        "documents": len(payload["documents"]),
        "enriched_by_llm": sum(1 for c in chunks if c.get("enrichment_source") not in (None, "heuristic")),
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("index built", extra={"kv": meta})
    return meta


def ensure_index(settings: Settings) -> None:
    """Rebuild the index when the processed chunks changed (used at API start-up)."""
    if not settings.structural_chunks_path.exists():
        if not (settings.index_dir / "index_meta.json").exists():
            raise FileNotFoundError(
                "No index found. Run the ingestion pipeline first: python -m ingest.run_all"
            )
        return
    build(settings)


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebuild even if the content hash is unchanged")
    args = ap.parse_args(argv)
    build(get_settings(), force=args.force)


if __name__ == "__main__":
    main()
