"""Claude enrichment helpers (no API calls): request shape, sanitizing, resumability, merge."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ingest.enrich_with_claude import TOOL_NAME, build_params, chunk_hash, load_records, sanitize, tool_input
from ingest.linker import merge

CHUNK = {
    "chunk_id": "c1::doc#0", "doc_id": "c1", "header": "Circular 1 (क, ख, ग) | FY 2083/84",
    "text": "इ.प्रा. निर्देशन नं. २/०८२ को बुँदा नं. ९ मा संशोधन ...",
    "doc_type": "circular", "fiscal_year": "2083/84", "title": "Circular 1", "section_no": None,
    "section_title": None, "language": "ne", "amends": [], "act_key": None, "amends_key": "ud_abc_2082",
    "family": "abc", "unit_kind": "document", "effective_from_fy": "2083/84",
}


def test_request_uses_prompt_caching_and_forced_tool() -> None:
    params = build_params(CHUNK, "FULL DOCUMENT TEXT", "claude-test")
    assert params["model"] == "claude-test"
    assert params["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "FULL DOCUMENT TEXT" in params["system"][1]["text"]
    assert params["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert "निर्देशन नं." in params["messages"][0]["content"]


def test_tool_input_and_sanitize() -> None:
    msg = SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=TOOL_NAME, input={"amends": ["x"]})])
    assert tool_input(msg) == {"amends": ["x"]}
    data = sanitize({
        "amends": ["ud_abc_2082 D.2.9", "customs_act s.1"],
        "effective_from_fy": "2083-84",
        "numeric_facts": [
            {"category": "threshold", "name": "t", "fiscal_year": "२०८३/८४", "source_quote": "q"},
            {"category": "threshold", "name": "no quote", "fiscal_year": "2083/84", "source_quote": ""},
        ],
        "hypothetical_questions": [{"question": "Q?", "language": "en"}, "bad"],
    }, CHUNK)
    assert data["amends"] == ["ud_abc_2082 D.2.9"]
    assert data["effective_from_fy"] == "2083/84"
    assert [f["fiscal_year"] for f in data["numeric_facts"]] == ["2083/84"]
    assert data["hypothetical_questions"] == [{"question": "Q?", "language": "en"}]


def test_records_are_resumable_and_hash_checked(tmp_path) -> None:
    path = tmp_path / "enriched.jsonl"
    good = {"chunk_id": "c1::doc#0", "chunk_hash": chunk_hash(CHUNK), "source": "claude",
            "data": {"context_summary": "From Claude", "amends": ["ud_abc_2082 D.14"], "topics": ["branches"]}}
    path.write_text(json.dumps(good) + "\n{corrupt\n", encoding="utf-8")
    records = load_records(path)
    assert set(records) == {"c1::doc#0"}

    merged = merge({"chunks": [CHUNK]}, records)[0]
    assert merged["enrichment_source"] == "claude" and merged["context_summary"] == "From Claude"
    # the directive number found in the circular text + the enrichment's judgement
    assert merged["amends"] == ["ud_abc_2082 D.14", "ud_abc_2082 D.2", "ud_abc_2082 D.2.9"]

    stale = dict(CHUNK, text="changed text")
    assert merge({"chunks": [stale]}, records)[0]["enrichment_source"] == "heuristic"
