"""Free enrichment (Gemini free tier / Ollama): prompt building, JSON parsing, quota stop, resumability."""

from __future__ import annotations

import json

import pytest

from app.glossary import glossary_terms
from ingest import enrich_free
from ingest.enrich_with_claude import load_records

CHUNK = {
    "chunk_id": "ud::d2p9#0", "doc_id": "ud", "parent_id": "ud::d2p9", "header": "Unified Directive 2082 | Directive 2",
    "text": "(१) कर्जा नोक्सानी व्यवस्था ...", "doc_type": "directive", "fiscal_year": "2082/83",
    "title": "Unified Directive 2082", "section_no": "2.9", "section_title": "कर्जा नोक्सानी", "language": "ne",
    "amends": [],
}
PAYLOAD = {"parents": {"ud::d2p9": {"header": "Unified Directive 2082 | Directive 2",
                                    "text": "९. कर्जा नोक्सानी ... (१) ... (२) ..."}},
           "chunks": [CHUNK, dict(CHUNK, chunk_id="ud::d2p9#1", text="(२) ...")]}


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, quota_after: int | None = None) -> None:
        self.calls = 0
        self.quota_after = quota_after

    def enrich(self, prompt: str) -> dict:
        self.calls += 1
        if self.quota_after is not None and self.calls > self.quota_after:
            raise enrich_free.DailyQuotaExhausted("per day quota")
        assert "CHUNK:" in prompt and "Full section around the chunk" in prompt
        return {"context_summary": "s", "amends": ["ud_abc_2082 d.14", "bogus ref"], "numeric_facts": [],
                "hypothetical_questions": [{"question": "Q?", "language": "en"}]}


def test_parse_json_tolerates_fences() -> None:
    assert enrich_free.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert enrich_free.parse_json('Here you go: {"a": 2} thanks') == {"a": 2}
    with pytest.raises(json.JSONDecodeError):
        enrich_free.parse_json("no json at all")


def test_run_writes_records_and_stops_on_daily_quota(tmp_path) -> None:
    settings = type("S", (), {"enriched_chunks_path": tmp_path / "enriched.jsonl"})()
    report = enrich_free.run(FakeProvider(quota_after=1), PAYLOAD["chunks"], PAYLOAD, settings)  # type: ignore[arg-type]
    assert report["succeeded"] == 1 and report["stopped"] and report["cost_usd"] == 0
    records = load_records(settings.enriched_chunks_path)
    assert set(records) == {"ud::d2p9#0"}
    assert records["ud::d2p9#0"]["source"] == "fake"
    assert records["ud::d2p9#0"]["data"]["amends"] == ["ud_abc_2082 D.14"]


def test_glossary_bridges_languages() -> None:
    terms = glossary_terms("कर्जा नोक्सानी व्यवस्था कायम गर्नु पर्नेछ")
    assert {"loan loss provision", "karja noksani"} <= set(terms)
    assert "कालोसूची" in glossary_terms("A blacklisted borrower cannot get a new loan")
