"""HTTP API with the SAMPLE index and a mocked LLM."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import RateLimiter


def test_health_and_meta(client: TestClient) -> None:
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["index_loaded"] and h["chunks"] > 0
    fys = client.get("/fiscal-years").json()
    assert fys["current"] == fys["default"]
    assert fys["available"][0] == "2083/84" and "2081/82" in fys["available"]
    assert "2083/84" in fys["calculator_years"]["loan_provision"]
    assert "2083/84" in fys["calculator_years"]["cash_limit"]
    docs = client.get("/documents").json()
    assert {d["doc_type"] for d in docs} == {"act", "directive", "circular", "notice"}
    assert all(d["chunks"] > 0 for d in docs)


def test_chat_returns_fy_sources_and_disclaimer(client: TestClient) -> None:
    r = client.post("/chat", json={"message": "What are the loan classification rules?", "session_id": "s1",
                                   "fiscal_year": "2083/84"})
    assert r.status_code == 200
    body = r.json()
    assert body["fiscal_year_used"] == "2083/84" and body["fy_source"] == "ui" and body["fy_warning"] is None
    assert body["answer"].startswith("**Applies to FY 2083/84**")
    assert "not legal or financial advice" in body["answer"]
    src = body["sources"][0]
    assert {"title", "doc_label", "section", "fy", "page", "snippet", "source_url", "institutions"} <= set(src)


def test_chat_fy_from_question_and_warning(client: TestClient) -> None:
    body = client.post("/chat", json={"message": "Loan loss provision rules in FY 2079/80?",
                                      "session_id": "s2"}).json()
    assert body["fiscal_year_requested"] == "2079/80" and body["fiscal_year_used"] == "2083/84"
    assert body["fy_source"] == "question" and "2079/80" in body["fy_warning"]


def test_chat_nepali(client: TestClient) -> None:
    body = client.post("/chat", json={"message": "नगद कारोबारको सीमा के हो?", "session_id": "s3"}).json()
    assert body["language"] == "ne"
    assert body["answer"].startswith("**लागू हुने आर्थिक वर्ष:")
    assert any(s["doc_type"] == "notice" for s in body["sources"])


def test_chat_romanized_nepali_is_answered_in_nepali(client: TestClient) -> None:
    body = client.post("/chat", json={"message": "karja noksani kati rakhnu parcha?", "session_id": "s3b"}).json()
    assert body["language"] == "ne"


def test_chat_calculation_via_tool_call(client: TestClient) -> None:
    body = client.post("/chat", json={"message": "How much provision for a loan of 1,000,000 overdue 4 months?",
                                      "session_id": "s4", "fiscal_year": "2083/84"}).json()
    calc = body["calculation"]
    assert calc["type"] == "loan_provision" and calc["fiscal_year"] == "2083/84"
    # SAMPLE 2082 edition: 1,000,000 pass @1% + 200,000 substandard (4 months) @25%
    assert calc["total_provision"] == 60000
    assert [r["category"] for r in calc["rows"]] == ["pass", "substandard"]


def test_chat_stream_sse(client: TestClient) -> None:
    with client.stream("POST", "/chat/stream", json={"message": "Can a blacklisted borrower get a new loan?",
                                                      "session_id": "s5"}) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text())
    events = [blk for blk in raw.split("\n\n") if blk.strip()]
    names = [e.split("\n")[0].removeprefix("event: ") for e in events]
    assert names[0] == "meta" and names[-1] == "done" and "token" in names
    done = json.loads(events[-1].split("data: ", 1)[1])
    tokens = "".join(json.loads(e.split("data: ", 1)[1])["text"] for e in events if e.startswith("event: token"))
    assert done["answer"] == tokens.strip()
    assert done["fiscal_year_used"] and done["sources"]


def test_follow_up_uses_memory(client: TestClient, engine) -> None:
    client.post("/chat", json={"message": "What is the minimum capital adequacy ratio?", "session_id": "mem"})
    client.post("/chat", json={"message": "and for microfinance?", "session_id": "mem"})
    assert len(engine.memory.get("mem")) == 2
    rewrite_calls = [m for m in engine.llm.calls if m[-1].content.startswith("Rewrite the user's latest message")]
    assert rewrite_calls  # the short follow-up was rewritten with conversation context


def test_calculate_endpoints(client: TestClient) -> None:
    r = client.post("/calculate/loan-provision", json={"fiscal_year": "2083/84", "loans": [
        {"amount": 100000, "months_overdue": 2}, {"amount": 100000, "category": "loss"}]})
    assert r.status_code == 200 and r.json()["total_provision"] == 105000
    r = client.post("/calculate/cash-limit", json={"fiscal_year": "2083/84", "amount": 500000})
    assert r.status_code == 200 and r.json()["at_or_above_limit"] is True and r.json()["limit"] == 500000
    r = client.post("/calculate/loan-provision", json={"fiscal_year": "2070/71", "loans": [{"amount": 1, "category": "pass"}]})
    assert r.status_code == 422 and "not been checked" in r.json()["detail"]


def test_validation_errors(client: TestClient) -> None:
    assert client.post("/chat", json={"message": "   "}).status_code == 422
    too_long = client.post("/chat", json={"message": "x" * 5000})
    assert too_long.status_code == 422 and "too long" in too_long.json()["detail"]
    assert client.post("/chat", json={"message": "hi", "fiscal_year": "abc"}).status_code == 422
    assert client.post("/chat", json={"message": "hi", "session_id": "bad id!"}).status_code == 422


def test_rate_limiter() -> None:
    rl = RateLimiter(2)
    assert rl.allow("ip") and rl.allow("ip") and not rl.allow("ip")
    assert rl.allow("other")
