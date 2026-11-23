"""
run_all.py
==========
Run the whole ingestion pipeline with one command:

    python -m ingest.run_all

Steps (each can be skipped):
    1. extract   data/raw/**            -> data/processed/extracted/*.json
    2. chunk     extracted text          -> data/processed/structural_chunks.json
    3. enrich    free LLM, one-time      -> data/processed/enriched_chunks.jsonl
                 (ENRICH_PROVIDER: gemini free tier | ollama local | none; claude is optional/paid)
    4. index     Chroma + BM25           -> data/index/
    5. review    regulatory figures      -> data/processed/bank_parameters.csv (verify them by hand)

If data/raw/manifest.yaml is missing it is generated from the NRB catalog first.

Examples:
    python -m ingest.run_all --skip-enrich                 # no LLM call at all
    python -m ingest.run_all --enrich-provider ollama      # local model, fully offline
    python -m ingest.run_all --enrich-limit 10             # try enrichment on 10 chunks
    python -m ingest.run_all --enrich-dry-run              # pending chunks / time (or cost for claude)
    python -m ingest.run_all --skip-extract --force-index  # re-chunk & rebuild index
"""

from __future__ import annotations

import argparse
import json
import logging

from app.config import get_settings
from app.logging_setup import setup_logging
from ingest import build_index, enrich_free, enrich_with_claude, generate_manifest, review_parameters
from ingest.chunker import chunk_corpus
from ingest.extract import ExtractedDoc, extract_all
from ingest.manifest_tools import load_manifest

log = logging.getLogger("run_all")


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-extract", action="store_true", help="reuse cached extraction (still re-chunks)")
    ap.add_argument("--skip-chunk", action="store_true", help="keep the existing structural_chunks.json")
    ap.add_argument("--skip-enrich", action="store_true", help="no LLM enrichment (heuristics + glossary only)")
    ap.add_argument("--enrich-provider", choices=["gemini", "ollama", "claude", "none"], default=None,
                    help="default: ENRICH_PROVIDER from .env (none = free heuristics)")
    ap.add_argument("--skip-index", action="store_true")
    ap.add_argument("--skip-review", action="store_true")
    ap.add_argument("--enrich-limit", type=int, default=None)
    ap.add_argument("--enrich-dry-run", action="store_true")
    ap.add_argument("--enrich-batch", action="store_true", help="claude provider only (paid)")
    ap.add_argument("--force-index", action="store_true")
    args = ap.parse_args(argv)

    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir = settings.processed_dir / "extracted"

    if not settings.manifest_path.exists():
        generate_manifest.build(settings.raw_dir)
    if not args.skip_chunk:
        entries = load_manifest(settings.raw_dir, settings.manifest_path)
        if not entries:
            raise SystemExit(f"No documents found in {settings.raw_dir}. Add PDFs/text files and a manifest.")
        if args.skip_extract:
            docs = [
                ExtractedDoc(**json.loads((extracted_dir / f"{e.doc_id}.json").read_text(encoding="utf-8")))
                for e in entries if (extracted_dir / f"{e.doc_id}.json").exists()
            ]
        else:
            docs = extract_all(entries, settings.raw_dir, extracted_dir)
        payload = chunk_corpus(entries, docs, settings.max_section_chars, settings.target_sub_chunk_chars)
        settings.structural_chunks_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("structural chunks written", extra={"kv": {"chunks": len(payload["chunks"]),
                                                            "documents": len(payload["documents"])}})

    provider = args.enrich_provider or settings.enrich_provider
    if not args.skip_enrich and provider != "none":
        enrich_args: list[str] = []
        if args.enrich_limit is not None:
            enrich_args += ["--limit", str(args.enrich_limit)]
        if args.enrich_dry_run:
            enrich_args.append("--dry-run")
        if provider == "claude":
            enrich_with_claude.main(enrich_args + (["--batch"] if args.enrich_batch else []))
        else:
            try:
                enrich_free.main(enrich_args + ["--provider", provider])
            except SystemExit as exc:  # provider not configured: continue with heuristic metadata
                log.warning("enrichment skipped", extra={"kv": {"reason": str(exc)}})

    if not args.skip_index:
        build_index.build(settings, force=args.force_index)

    if not args.skip_review:
        review_parameters.main([])
    log.info("pipeline finished")


if __name__ == "__main__":
    main()
