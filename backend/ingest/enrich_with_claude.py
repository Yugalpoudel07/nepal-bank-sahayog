"""
enrich_with_claude.py
=====================
Step 3: ONE-TIME offline enrichment of every chunk with the Anthropic API.

For each chunk we send the chunk plus its surrounding document text (the
document text is marked with prompt caching so consecutive chunks of the same
document reuse it) and force structured JSON output through a tool schema.

Claude returns: context_summary, plain explanations (English + Nepali),
keywords (English, Devanagari, romanized), 6 hypothetical questions,
amends / amended_by references, effective FY suggestions, topics and
numeric_facts (rates, thresholds, slabs, deadlines with FY + source quote).

Properties
* resumable — chunks already enriched (same chunk id AND same content hash)
  are skipped; results are appended to data/processed/enriched_chunks.jsonl
  one line at a time, so an interrupted run loses nothing;
* retries with exponential backoff;
* --limit N for test runs, --dry-run for a cost estimate, --batch to use the
  Message Batches API (50% cheaper, asynchronous);
* writes a summary report to data/processed/enrichment_report.json.

After this step the runtime app never calls Claude again. Without an
ANTHROPIC_API_KEY this script exits cleanly and build_index falls back to
deterministic heuristic metadata.

Usage (from backend/):
    python -m ingest.enrich_with_claude --dry-run
    python -m ingest.enrich_with_claude --limit 5
    python -m ingest.enrich_with_claude                 # everything not yet enriched
    python -m ingest.enrich_with_claude --batch         # Message Batches API
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.acts import normalize_ref
from app.config import Settings, get_settings
from app.fiscal_year import normalize_fy_label
from app.logging_setup import setup_logging

log = logging.getLogger("enrich")

TOOL_NAME = "record_chunk_enrichment"
MAX_DOC_CHARS = 120_000
OUTPUT_TOKEN_ESTIMATE = 1100
SYSTEM_TOKEN_ESTIMATE = 900

SYSTEM_PROMPT = """You are an expert on Nepal Rastra Bank (NRB) regulation: the banking Acts (Bank and Financial
Institutions Act 2073, NRB Act 2058, Payment and Settlement Act 2075, ...), NRB unified directives for
class A/B/C banks, microfinance, the infrastructure bank and payment providers, the foreign-exchange unified
circular, NRB circulars, notices and monetary policy. You enrich ONE chunk of a document so that a retrieval system
can find it and answer questions about it correctly.

Rules:
- Use ONLY the chunk and the document text provided. Never invent rates, limits or dates.
- Fiscal years use the Bikram Sambat form "2083/84" (Shrawan 1 to Ashadh end).
- References use exactly this format: "bafia s.30" (Act section), "ud_abc_2082 D.2" (directive 2 of the
  2082 unified directive for class A/B/C banks), "ud_abc_2082 D.2.9" (its point 9); families: abc, mfi,
  nifra, psd, fx, ncbl.
- "amends": only provisions this chunk CHANGES (a circular inserting, substituting or repealing a directive
  point). Mentioned is not amended. Acts and directive text amend nothing.
- "amended_by": amendments the chunk text itself says were made to it; otherwise empty.
- effective_from_fy / effective_to_fy: only when the text makes them clear, otherwise null.
- numeric_facts: every ratio, limit, provisioning rate, interest-rate rule, deadline or fee in the chunk,
  with category "rate", "threshold", "deadline", "fee" or "other", its fiscal year and a short verbatim
  source_quote.
- hypothetical_questions: exactly 6 realistic questions a bank customer, business owner or bank employee
  would ask that this chunk answers: 3 English, 2 Nepali (Devanagari), 1 romanized Nepali.
- plain_explanation_ne and keywords_ne in Devanagari; keywords_romanized as Nepalis type them in Latin
  script (e.g. "karja noksani", "byajdar", "kalo suchi", "nagad karobar").
Call the tool exactly once."""

_STR_LIST = {"type": "array", "items": {"type": "string"}}
ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "context_summary": {"type": "string", "description": "1-2 sentences situating the chunk in its document"},
        "plain_explanation": {"type": "string", "description": "simple English explanation"},
        "plain_explanation_ne": {"type": "string", "description": "simple Nepali explanation (Devanagari)"},
        "keywords_en": _STR_LIST,
        "keywords_ne": _STR_LIST,
        "keywords_romanized": _STR_LIST,
        "hypothetical_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "language": {"type": "string", "enum": ["en", "ne", "romanized"]},
                },
                "required": ["question", "language"],
            },
        },
        "amends": _STR_LIST,
        "amended_by": _STR_LIST,
        "effective_from_fy": {"type": ["string", "null"]},
        "effective_to_fy": {"type": ["string", "null"]},
        "topics": _STR_LIST,
        "numeric_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["rate", "threshold", "deadline", "fee", "other"],
                    },
                    "name": {"type": "string"},
                    "fiscal_year": {"type": "string"},
                    "lower": {"type": ["number", "null"]},
                    "upper": {"type": ["number", "null"]},
                    "rate_percent": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                    "source_quote": {"type": "string"},
                },
                "required": ["category", "name", "fiscal_year", "source_quote"],
            },
        },
    },
    "required": [
        "context_summary", "plain_explanation", "plain_explanation_ne", "keywords_en", "keywords_ne",
        "keywords_romanized", "hypothetical_questions", "amends", "numeric_facts",
    ],
}


# ── helpers ───────────────────────────────────────────────────────────────────
def chunk_hash(chunk: dict) -> str:
    """Content hash used to decide whether a chunk needs (re-)enrichment."""
    key = "\x1f".join(str(chunk.get(k) or "") for k in ("header", "text", "doc_type", "fiscal_year"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def load_records(path: Path) -> dict[str, dict]:
    """Latest enrichment record per chunk id (later lines win)."""
    records: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("skipping corrupt line in enriched_chunks.jsonl")
                        continue
                    records[rec["chunk_id"]] = rec
    return records


def document_texts(payload: dict) -> dict[str, str]:
    """doc_id -> full document text rebuilt from parent units, in order."""
    texts: dict[str, list[str]] = defaultdict(list)
    for pid, parent in payload["parents"].items():
        texts[pid.split("::", 1)[0]].append(parent["header"] + "\n" + parent["text"])
    return {doc: "\n\n".join(parts)[:MAX_DOC_CHARS] for doc, parts in texts.items()}


def sanitize(data: dict, chunk: dict) -> dict:
    """Normalize references and FY labels returned by Claude; drop malformed facts."""
    out = dict(data)
    for key in ("amends", "amended_by"):
        out[key] = [r for r in (normalize_ref(x) for x in data.get(key) or []) if r]
    for key in ("effective_from_fy", "effective_to_fy"):
        out[key] = normalize_fy_label(data.get(key)) if data.get(key) else None
    facts = []
    for f in data.get("numeric_facts") or []:
        fy = normalize_fy_label(f.get("fiscal_year")) or chunk.get("fiscal_year")
        if not fy or not f.get("source_quote"):
            continue
        facts.append({**f, "fiscal_year": fy})
    out["numeric_facts"] = facts
    out["hypothetical_questions"] = [
        q for q in data.get("hypothetical_questions") or [] if isinstance(q, dict) and q.get("question")
    ]
    return out


def _user_content(chunk: dict) -> str:
    meta = {
        k: chunk.get(k)
        for k in ("doc_type", "title", "fiscal_year", "section_no", "section_title", "language", "amends")
    }
    return (
        "Chunk metadata (amends = references detected by a regex, verify them):\n"
        f"{json.dumps(meta, ensure_ascii=False)}\n\n"
        f"Chunk:\n{chunk['header']}\n{chunk['text']}"
    )


def build_params(chunk: dict, doc_text: str, model: str) -> dict:
    """Messages API parameters for one chunk (document text cached)."""
    return {
        "model": model,
        "max_tokens": 4096,
        "system": [
            {"type": "text", "text": SYSTEM_PROMPT},
            {
                "type": "text",
                "text": f"<document>\n{doc_text}\n</document>",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "tools": [{
            "name": TOOL_NAME,
            "description": "Record the enrichment for this chunk.",
            "input_schema": ENRICHMENT_SCHEMA,
        }],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": _user_content(chunk)}],
    }


def tool_input(message: Any) -> dict:
    """Extract the forced tool call's input from a Message."""
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return dict(block.input)
    raise ValueError("response contained no tool call")


def _record(chunk: dict, data: dict, model: str) -> dict:
    return {
        "chunk_id": chunk["chunk_id"],
        "chunk_hash": chunk_hash(chunk),
        "source": "claude",
        "model": model,
        "enriched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "data": sanitize(data, chunk),
    }


def _est_tokens(text: str) -> int:
    devanagari = sum(1 for c in text if "\u0900" <= c <= "\u097F")
    return int((len(text) - devanagari) / 4 + devanagari / 2.2) + 1


# ── usage / cost accounting ───────────────────────────────────────────────────
class Usage:
    """Accumulates token usage and computes cost."""

    def __init__(self, price_in: float, price_out: float, batch: bool) -> None:
        self.price_in, self.price_out = price_in, price_out
        self.discount = 0.5 if batch else 1.0
        self.input = self.output = self.cache_write = self.cache_read = 0

    def add(self, usage: Any) -> None:
        self.input += getattr(usage, "input_tokens", 0) or 0
        self.output += getattr(usage, "output_tokens", 0) or 0
        self.cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0

    def cost(self) -> float:
        per_in = self.price_in / 1e6
        total = (
            self.input * per_in
            + self.cache_write * per_in * 1.25
            + self.cache_read * per_in * 0.1
            + self.output * self.price_out / 1e6
        )
        return round(total * self.discount, 4)

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input, "output_tokens": self.output,
            "cache_write_tokens": self.cache_write, "cache_read_tokens": self.cache_read,
            "estimated_cost_usd": self.cost(),
        }


def estimate(pending: list[dict], docs: dict[str, str], price_in: float, price_out: float, batch: bool) -> dict:
    """Rough cost estimate before spending anything."""
    u = Usage(price_in, price_out, batch)
    seen_docs: set[str] = set()
    for c in pending:
        doc_tokens = _est_tokens(docs.get(c["doc_id"], ""))
        if c["doc_id"] in seen_docs:
            u.cache_read += doc_tokens
        else:
            u.cache_write += doc_tokens
            seen_docs.add(c["doc_id"])
        u.input += SYSTEM_TOKEN_ESTIMATE + _est_tokens(c["header"] + c["text"])
        u.output += OUTPUT_TOKEN_ESTIMATE
    return {"pending_chunks": len(pending), "documents": len(seen_docs), **u.as_dict()}


# ── API calls ─────────────────────────────────────────────────────────────────
def call_with_retries(client: Any, params: dict, attempts: int = 6) -> Any:
    """Messages API call with exponential backoff on rate limits, overload and network errors."""
    import anthropic

    for attempt in range(attempts):
        try:
            return client.messages.create(**params)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            wait = min(60, 2 ** attempt) + random.random()
            log.warning("retrying", extra={"kv": {"attempt": attempt + 1, "wait_s": round(wait, 1), "error": type(exc).__name__}})
            time.sleep(wait)
        except anthropic.APIStatusError as exc:
            if exc.status_code in (408, 409, 429, 529) or exc.status_code >= 500:
                wait = min(60, 2 ** attempt) + random.random()
                log.warning("retrying", extra={"kv": {"attempt": attempt + 1, "status": exc.status_code}})
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("giving up after repeated API errors")


def run_sync(client: Any, pending: list[dict], docs: dict[str, str], settings: Settings, usage: Usage) -> tuple[int, list[str]]:
    """Enrich chunks one by one, appending each result immediately."""
    ok, failed = 0, []
    out_path = settings.enriched_chunks_path
    with out_path.open("a", encoding="utf-8") as out:
        for i, chunk in enumerate(pending, 1):
            try:
                msg = call_with_retries(client, build_params(chunk, docs.get(chunk["doc_id"], ""), settings.claude_model))
                usage.add(msg.usage)
                out.write(json.dumps(_record(chunk, tool_input(msg), settings.claude_model), ensure_ascii=False) + "\n")
                out.flush()
                ok += 1
                log.info("enriched", extra={"kv": {"n": f"{i}/{len(pending)}", "chunk": chunk["chunk_id"]}})
            except Exception as exc:
                failed.append(chunk["chunk_id"])
                log.error("enrichment failed", extra={"kv": {"chunk": chunk["chunk_id"], "error": str(exc)[:300]}})
    return ok, failed


def _custom_id(chunk_id: str) -> str:
    return "c_" + hashlib.sha1(chunk_id.encode("utf-8")).hexdigest()[:40]


def run_batch(client: Any, pending: list[dict], docs: dict[str, str], settings: Settings, usage: Usage,
              poll_seconds: int) -> tuple[int, list[str]]:
    """Submit (or resume) a Message Batch and write results when it ends."""
    state_path = settings.processed_dir / "enrichment_batch.json"
    by_custom = {_custom_id(c["chunk_id"]): c for c in pending}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        log.info("resuming batch", extra={"kv": {"batch_id": state["batch_id"]}})
    else:
        requests = [
            {"custom_id": cid, "params": build_params(c, docs.get(c["doc_id"], ""), settings.claude_model)}
            for cid, c in by_custom.items()
        ]
        batch = client.messages.batches.create(requests=requests)
        state = {"batch_id": batch.id, "chunk_ids": {cid: c["chunk_id"] for cid, c in by_custom.items()}}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        log.info("batch submitted", extra={"kv": {"batch_id": batch.id, "requests": len(requests)}})

    while True:
        batch = client.messages.batches.retrieve(state["batch_id"])
        if batch.processing_status == "ended":
            break
        log.info("batch in progress", extra={"kv": {"counts": str(batch.request_counts)}})
        time.sleep(poll_seconds)

    chunk_lookup = {c["chunk_id"]: c for c in pending}
    ok, failed = 0, []
    with settings.enriched_chunks_path.open("a", encoding="utf-8") as out:
        for entry in client.messages.batches.results(state["batch_id"]):
            chunk_id = state["chunk_ids"].get(entry.custom_id)
            chunk = chunk_lookup.get(chunk_id or "")
            if chunk is None:
                continue
            if entry.result.type == "succeeded":
                try:
                    usage.add(entry.result.message.usage)
                    out.write(json.dumps(_record(chunk, tool_input(entry.result.message), settings.claude_model),
                                         ensure_ascii=False) + "\n")
                    ok += 1
                    continue
                except ValueError as exc:
                    log.error("bad batch result", extra={"kv": {"chunk": chunk_id, "error": str(exc)}})
            failed.append(chunk_id or entry.custom_id)
    state_path.unlink()
    return ok, failed


# ── entry point ───────────────────────────────────────────────────────────────
def pending_chunks(settings: Settings) -> tuple[list[dict], dict[str, str], int]:
    """(chunks needing enrichment, document texts, number already done)."""
    payload = json.loads(settings.structural_chunks_path.read_text(encoding="utf-8"))
    done = load_records(settings.enriched_chunks_path)
    pending = [
        c for c in payload["chunks"]
        if not (c["chunk_id"] in done and done[c["chunk_id"]].get("chunk_hash") == chunk_hash(c))
    ]
    pending.sort(key=lambda c: (c["doc_id"], c["chunk_id"]))  # same-document chunks together -> cache hits
    return pending, document_texts(payload), len(payload["chunks"]) - len(pending)


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="enrich at most N chunks (test runs)")
    ap.add_argument("--dry-run", action="store_true", help="print a cost estimate and exit")
    ap.add_argument("--batch", action="store_true", help="use the Message Batches API (async, 50%% cheaper)")
    ap.add_argument("--poll-seconds", type=int, default=60)
    ap.add_argument("--price-in", type=float, default=3.0, help="USD per million input tokens (check current pricing)")
    ap.add_argument("--price-out", type=float, default=15.0, help="USD per million output tokens (check current pricing)")
    args = ap.parse_args(argv)

    if not settings.structural_chunks_path.exists():
        raise SystemExit("structural_chunks.json not found. Run: python -m ingest.run_all --skip-enrich first.")
    pending, docs, already = pending_chunks(settings)
    if args.limit is not None:
        pending = pending[: args.limit]

    est = estimate(pending, docs, args.price_in, args.price_out, args.batch)
    log.info("estimate", extra={"kv": {"model": settings.claude_model, "already_enriched": already, **est}})
    if args.dry_run:
        print(json.dumps({"model": settings.claude_model, "already_enriched": already, **est}, indent=2))
        return
    if not pending:
        log.info("nothing to enrich; all chunks are up to date")
        return
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY not set: skipping Claude enrichment. "
                    "build_index will use heuristic metadata for un-enriched chunks.")
        return

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    usage = Usage(args.price_in, args.price_out, args.batch)
    started = time.time()
    if args.batch:
        ok, failed = run_batch(client, pending, docs, settings, usage, args.poll_seconds)
    else:
        ok, failed = run_sync(client, pending, docs, settings, usage)

    report = {
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "model": settings.claude_model,
        "mode": "batch" if args.batch else "sync",
        "already_enriched": already,
        "attempted": len(pending),
        "succeeded": ok,
        "failed": failed,
        "seconds": round(time.time() - started, 1),
        **usage.as_dict(),
    }
    (settings.processed_dir / "enrichment_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("enrichment finished", extra={"kv": {k: v for k, v in report.items() if k != "failed"} | {"failed": len(failed)}})


if __name__ == "__main__":
    main()
