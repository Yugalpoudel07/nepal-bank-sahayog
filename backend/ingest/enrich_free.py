"""
enrich_free.py
==============
Step 3 (free): one-time chunk enrichment without paying anything.

Two zero-cost providers produce exactly the same JSON as the Claude step
(context summary, plain explanations in English and Nepali, keywords in three
scripts, hypothetical questions, amendment references, effective FYs,
numeric facts):

* gemini  — Google Gemini API **free tier** (the same GOOGLE_API_KEY the app
            uses). Do NOT link a billing account to that Google project, so it
            can never be charged. Free-tier limits are per minute and per day;
            the script paces itself (ENRICH_REQUESTS_PER_MINUTE) and, when the
            daily quota is used up, stops cleanly. Run it again the next day:
            finished chunks are skipped.
* ollama  — a local open model through Ollama (https://ollama.com), e.g.
            `ollama pull qwen2.5:7b`. Unlimited and private, but slower and
            weaker in Nepali than Gemini.

Chunks without enrichment still work: build_index falls back to deterministic
heuristics plus the bilingual banking glossary.

Usage (from backend/):
    python -m ingest.enrich_free --dry-run
    python -m ingest.enrich_free --limit 5               # try a few first
    python -m ingest.enrich_free                         # provider from ENRICH_PROVIDER (default gemini)
    python -m ingest.enrich_free --provider ollama
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import time
import urllib.request
from collections import defaultdict
from typing import Any

from app.config import Settings, get_settings
from app.logging_setup import setup_logging
from ingest.enrich_with_claude import ENRICHMENT_SCHEMA, chunk_hash, pending_chunks, sanitize

log = logging.getLogger("enrich_free")

MAX_SECTION_CONTEXT = 6000
MAX_TOC_CHARS = 3000

INSTRUCTIONS = """You are an expert on Nepal Rastra Bank (NRB) regulation: the banking Acts (Bank and Financial
Institutions Act 2073, NRB Act 2058, Payment and Settlement Act 2075, ...), NRB unified directives for
class A/B/C banks, microfinance, the infrastructure bank and payment providers, the foreign-exchange unified
circular, NRB circulars, notices and monetary policy. Enrich ONE chunk of a document so a search system can find it
and answer questions about it correctly. Reply with ONE JSON object that follows the schema.

Rules:
- Use ONLY the chunk and the context given. Never invent rates, limits or dates.
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
  script (e.g. "karja noksani", "byajdar", "kalo suchi", "nagad karobar")."""


class DailyQuotaExhausted(Exception):
    """The free daily quota is used up; progress is saved and the run can resume tomorrow."""


# ── context (kept small: free tiers limit tokens per minute) ─────────────────
def build_contexts(payload: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(doc_id -> table of contents, parent_id -> parent section text)."""
    toc: dict[str, list[str]] = defaultdict(list)
    for pid, parent in payload["parents"].items():
        toc[pid.split("::", 1)[0]].append(parent["header"])
    tocs = {doc: "\n".join(lines)[:MAX_TOC_CHARS] for doc, lines in toc.items()}
    parents = {pid: p["text"][:MAX_SECTION_CONTEXT] for pid, p in payload["parents"].items()}
    return tocs, parents


def build_prompt(chunk: dict, toc: str, parent_text: str) -> str:
    meta = {k: chunk.get(k) for k in ("doc_type", "title", "fiscal_year", "section_no", "section_title",
                                      "language", "amends")}
    parts = [
        f"Document outline:\n{toc}",
        f"Chunk metadata (amends = references found by a regex, verify them):\n{json.dumps(meta, ensure_ascii=False)}",
    ]
    if parent_text and parent_text.strip() != chunk["text"].strip():
        parts.append(f"Full section around the chunk:\n{parent_text}")
    parts.append(f"CHUNK:\n{chunk['header']}\n{chunk['text']}")
    return "\n\n".join(parts)


def parse_json(text: str) -> dict:
    """Parse a JSON object from model output (tolerates code fences / surrounding prose)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


# ── providers ─────────────────────────────────────────────────────────────────
class GeminiFree:
    """Gemini API free tier via the google-genai SDK, with pacing and quota handling."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        if not settings.google_api_key:
            raise SystemExit("GOOGLE_API_KEY is not set in backend/.env (get a free key at aistudio.google.com).")
        from google import genai

        self.model = settings.enrich_gemini_model
        self._client = genai.Client(api_key=settings.google_api_key)
        self._interval = 60.0 / max(1, settings.enrich_requests_per_minute)
        self._last = 0.0
        self._schema_in_config = True

    def _config(self) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = {"system_instruction": INSTRUCTIONS, "response_mime_type": "application/json",
                                  "temperature": 0.1}
        if self._schema_in_config:
            kwargs["response_json_schema"] = ENRICHMENT_SCHEMA
        return types.GenerateContentConfig(**kwargs)

    def enrich(self, prompt: str) -> dict:
        from google.genai import errors

        if not self._schema_in_config:
            prompt += "\n\nJSON schema to follow:\n" + json.dumps(ENRICHMENT_SCHEMA)
        for attempt in range(6):
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self._client.models.generate_content(model=self.model, contents=prompt, config=self._config())
                return parse_json(resp.text or "")
            except errors.ClientError as exc:
                msg = str(exc)
                if exc.code == 429:
                    if re.search(r"per ?day|PerDay|daily", msg, re.IGNORECASE):
                        raise DailyQuotaExhausted(msg[:300]) from exc
                    log.warning("per-minute limit hit; waiting 60 s", extra={"kv": {"attempt": attempt + 1}})
                    time.sleep(60)
                    continue
                if exc.code == 400 and self._schema_in_config and "schema" in msg.lower():
                    log.warning("model rejected the response schema; embedding it in the prompt instead")
                    self._schema_in_config = False
                    prompt += "\n\nJSON schema to follow:\n" + json.dumps(ENRICHMENT_SCHEMA)
                    continue
                raise
            except errors.ServerError as exc:
                log.warning("server error; retrying", extra={"kv": {"attempt": attempt + 1, "code": exc.code}})
                time.sleep(min(60, 5 * 2 ** attempt))
            except json.JSONDecodeError:
                log.warning("invalid JSON from model; retrying", extra={"kv": {"attempt": attempt + 1}})
        raise RuntimeError("giving up after repeated errors")


class OllamaLocal:
    """Local model through Ollama's HTTP API (structured output via the `format` schema)."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.ollama_model
        self.url = settings.ollama_url.rstrip("/") + "/api/chat"
        try:
            urllib.request.urlopen(settings.ollama_url.rstrip("/") + "/api/tags", timeout=5).read()
        except Exception as exc:
            raise SystemExit(
                f"Ollama is not reachable at {settings.ollama_url}. Install it from ollama.com, run "
                f"`ollama pull {self.model}`, and keep the Ollama app running."
            ) from exc

    def enrich(self, prompt: str) -> dict:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": prompt}],
            "format": ENRICHMENT_SCHEMA,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }).encode("utf-8")
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=600) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return parse_json(data["message"]["content"])
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("invalid JSON from model; retrying", extra={"kv": {"attempt": attempt + 1, "error": str(exc)}})
        raise RuntimeError("giving up after repeated invalid responses")


def make_provider(name: str, settings: Settings) -> GeminiFree | OllamaLocal:
    if name == "gemini":
        return GeminiFree(settings)
    if name == "ollama":
        return OllamaLocal(settings)
    raise SystemExit(f"Unknown free provider {name!r}; use gemini or ollama.")


# ── run ───────────────────────────────────────────────────────────────────────
def run(provider: GeminiFree | OllamaLocal, pending: list[dict], payload: dict, settings: Settings) -> dict:
    """Enrich pending chunks one by one, appending each result immediately (resumable)."""
    tocs, parents = build_contexts(payload)
    ok, failed, stopped = 0, [], None
    started = time.time()
    with settings.enriched_chunks_path.open("a", encoding="utf-8") as out:
        for i, chunk in enumerate(pending, 1):
            prompt = build_prompt(chunk, tocs.get(chunk["doc_id"], ""), parents.get(chunk["parent_id"], ""))
            try:
                data = provider.enrich(prompt)
            except DailyQuotaExhausted as exc:
                stopped = f"daily free quota reached: {exc}"
                log.warning("daily free quota reached; progress saved. Run the same command again tomorrow "
                            "(quotas reset at midnight Pacific time).")
                break
            except Exception as exc:
                failed.append(chunk["chunk_id"])
                log.error("enrichment failed", extra={"kv": {"chunk": chunk["chunk_id"], "error": str(exc)[:300]}})
                continue
            record = {
                "chunk_id": chunk["chunk_id"], "chunk_hash": chunk_hash(chunk), "source": provider.name,
                "model": provider.model,
                "enriched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "data": sanitize(data, chunk),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            ok += 1
            log.info("enriched", extra={"kv": {"n": f"{i}/{len(pending)}", "chunk": chunk["chunk_id"]}})
    return {"provider": provider.name, "model": provider.model, "attempted": len(pending), "succeeded": ok,
            "failed": failed, "stopped": stopped, "seconds": round(time.time() - started, 1), "cost_usd": 0}


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["gemini", "ollama"], default=None,
                    help="default: ENRICH_PROVIDER from .env")
    ap.add_argument("--limit", type=int, default=None, help="enrich at most N chunks (test runs)")
    ap.add_argument("--dry-run", action="store_true", help="show how many chunks are pending and the run time")
    args = ap.parse_args(argv)

    provider_name = args.provider or settings.enrich_provider
    if provider_name not in ("gemini", "ollama"):
        log.info("ENRICH_PROVIDER is not gemini/ollama; skipping free enrichment",
                 extra={"kv": {"provider": provider_name}})
        return
    if not settings.structural_chunks_path.exists():
        raise SystemExit("structural_chunks.json not found. Run: python -m ingest.run_all --skip-enrich first.")

    payload = json.loads(settings.structural_chunks_path.read_text(encoding="utf-8"))
    pending, _docs, already = pending_chunks(settings)
    if args.limit is not None:
        pending = pending[: args.limit]

    if args.dry_run:
        minutes = len(pending) / max(1, settings.enrich_requests_per_minute)
        print(json.dumps({
            "provider": provider_name, "already_enriched": already, "pending_chunks": len(pending),
            "requests": len(pending), "estimated_minutes_at_rpm": round(minutes, 1), "cost_usd": 0,
            "note": "Gemini free tier also has a requests-per-day cap; check yours in Google AI Studio. "
                    "When it is reached the run stops and resumes next time.",
        }, indent=2))
        return
    if not pending:
        log.info("nothing to enrich; all chunks are up to date")
        return

    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    report = run(make_provider(provider_name, settings), pending, payload, settings)
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    report["already_enriched"] = already
    (settings.processed_dir / "enrichment_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("free enrichment finished", extra={"kv": {k: v for k, v in report.items() if k != "failed"}
                                                 | {"failed": len(report["failed"])}})


if __name__ == "__main__":
    main()
