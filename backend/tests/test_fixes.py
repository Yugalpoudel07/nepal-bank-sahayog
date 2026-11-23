"""Regression tests for the bugs found in the November 2026 testing phase."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.config import get_settings
from app.llm import QUOTA_MESSAGE, LLMReply, ToolCall, _friendly
from app.main import RateLimiter
from app.rag_engine import fix_digits
from app.retrieval import Hit, Retriever
from tests.conftest import FakeLLM
from tools.calculators import NO_CALCULATION
from tools.parameters import CSV_COLUMNS, ParametersUnavailable, ParameterTable

NE_DISCLAIMER = "*यो NRB कागजातमा आधारित सामान्य जानकारी हो, कानुनी वा वित्तीय सल्लाह होइन — आफ्नो बैंकसँग बुझ्नुहोस्।*"


# ── bug 1: a long provision's main rule (its opening piece) was dropped from the context ──────────────
def _piece(pid: str, n: int, text: str) -> dict:
    return {"chunk_id": f"{pid}#{n}", "parent_id": pid, "text": text}


def test_opening_piece_of_long_provision_is_added() -> None:
    long_pid, short_pid = "ud::d2p9", "ud::d2p4"
    pieces = [_piece(long_pid, 0, "heading"), _piece(long_pid, 1, "rate table " * 40), _piece(long_pid, 3, "exceptions " * 40)]
    short = {"chunk_id": short_pid, "parent_id": short_pid, "text": "short rule " * 30}
    store = SimpleNamespace(
        chunks={c["chunk_id"]: c for c in [*pieces, short]},
        parents={long_pid: {"text": "x" * 60000}, short_pid: {"text": short["text"]}},
    )
    r = Retriever.__new__(Retriever)
    r.store, r.settings = store, get_settings()
    out = r._with_lead_pieces([Hit(pieces[2], 1.0), Hit(short, 0.9)])
    # the heading (#0) is too short to be the opening piece; #1 holds the main rule
    assert [h.id for h in out] == [f"{long_pid}#3", f"{long_pid}#1", short_pid]
    # already selected: not added twice
    assert [h.id for h in r._with_lead_pieces([Hit(pieces[1], 1.0), Hit(pieces[2], 0.5)])] == [f"{long_pid}#1", f"{long_pid}#3"]


# ── bug 2: English answers started with the Nepali FY line / ended with the Nepali disclaimer ───────────
class WrongFormatLLM(FakeLLM):
    """Writes both FY headings, a sentence mentioning another FY, foreign digits and the Nepali disclaimer."""

    TEXT = ("**लागू हुने आर्थिक वर्ष: 2083/84**\n\n**Applies to FY 2083/84**\n\nThe rate is 1 percent. "
            "Applies to FY 2081/82 it was 1.10 percent. Limit ២,५००.\n\n" + NE_DISCLAIMER)

    def complete(self, messages, tools=None, force_tool=False):
        if messages[-1].content.startswith("Rewrite the user's latest message"):
            return LLMReply(messages[-1].content.split("Latest message: ")[-1].split("\n")[0])
        return LLMReply(self.TEXT)

    def stream(self, messages):
        for i in range(0, len(self.TEXT), 7):
            yield self.TEXT[i: i + 7]


def _check_english(answer: str) -> None:
    assert answer.startswith("**Applies to FY 2083/84**\n\nThe rate is 1 percent.")
    assert "लागू हुने" not in answer and "यो NRB कागजातमा" not in answer
    assert "Applies to FY 2081/82 it was 1.10 percent" in answer  # text inside the answer is kept
    assert "२,५००" in answer and "២" not in answer
    assert answer.count("not legal or financial advice") == 1 and answer.endswith("ask your bank._")


def test_answer_has_one_fy_line_and_disclaimer_in_reply_language(engine) -> None:
    engine.llm = WrongFormatLLM()
    _check_english(engine.answer("What is the loan loss provision rate?", "fmt1", "2083/84", "en").answer)


def test_streamed_answer_has_one_fy_line_and_disclaimer_in_reply_language(engine) -> None:
    engine.llm = WrongFormatLLM()
    events = list(engine.answer_stream("What is the loan loss provision rate?", "fmt2", "2083/84", "en"))
    tokens = "".join(d["text"] for e, d in events if e == "token")
    done = [d for e, d in events if e == "done"][0]
    _check_english(done["answer"])
    assert done["answer"] == tokens.strip()


def test_system_prompt_shows_only_the_reply_language_format() -> None:
    from app.prompts import SYSTEM_PROMPT

    assert "लागू हुने" not in SYSTEM_PROMPT and "सल्लाह होइन" not in SYSTEM_PROMPT


def test_fix_digits() -> None:
    assert fix_digits("២,५००.०० ۶,००० ٣ 2,500") == "२,५००.०० ६,००० ३ 2,500"


# ── bug 3: the model computed a provision itself instead of calling the calculator ────────────────────
class RecordingLLM(FakeLLM):
    def __init__(self, choice: str) -> None:
        super().__init__()
        self.choice, self.forced = choice, []

    def complete(self, messages, tools=None, force_tool=False):
        if tools:
            self.forced.append(force_tool)
            args = {} if self.choice == NO_CALCULATION else {"loans": [{"amount": 1000000, "months_overdue": 4}]}
            return LLMReply("", [ToolCall("c1", self.choice, args)])
        return super().complete(messages)


def test_calculation_questions_force_a_tool_choice(engine) -> None:
    engine.llm = RecordingLLM("calculate_loan_provision")
    res = engine.answer("How much provision for a loan of Rs 10,00,000 overdue 4 months?", "calc1", "2083/84")
    assert engine.llm.forced == [True] and res.calculation and res.calculation["type"] == "loan_provision"


def test_no_calculation_choice_answers_from_documents(engine) -> None:
    engine.llm = RecordingLLM(NO_CALCULATION)
    res = engine.answer("What is the provision rate for a loan of 1,000,000 class?", "calc2", "2083/84")
    assert engine.llm.forced == [True] and res.calculation is None and "Here is the answer" in res.answer


# ── quota exhausted: users saw "Gemini request failed: ChatGoogleGenerativeAIError" ──────────────────
def test_quota_errors_get_a_clear_message() -> None:
    assert str(_friendly(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))) == QUOTA_MESSAGE
    other = str(_friendly(RuntimeError("500 internal")))
    assert "Error" not in other and "try again" in other


def test_stream_shows_quota_message(client) -> None:
    import app.llm as llm_mod

    class QuotaLLM(FakeLLM):
        def stream(self, messages):
            raise llm_mod.LLMUnavailable(QUOTA_MESSAGE)

    client.app.state.engine.llm = QuotaLLM()
    with client.stream("POST", "/chat/stream", json={"message": "What is the CRR?", "session_id": "q1"}) as r:
        raw = "".join(r.iter_text())
    err = [b for b in raw.split("\n\n") if b.startswith("event: error")][0]
    assert json.loads(err.split("data: ", 1)[1])["detail"] == QUOTA_MESSAGE


# ── calculator: an older edition's verified figures stood in for a newer, unverified edition ─────────
def _write(path: Path, rows: list[dict]) -> ParameterTable:
    import csv

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in CSV_COLUMNS} for r in rows])
    return ParameterTable(path)


def test_older_verified_edition_does_not_replace_newer_unverified_one(tmp_path: Path) -> None:
    base = {"parameter": "loan_loss_provision", "category": "pass", "unit": "percent", "family": "abc"}
    table = _write(tmp_path / "p.csv", [
        {**base, "value": "1.10", "effective_from_fy": "2081/82", "effective_to_fy": "2082/83", "verified": "true"},
        {**base, "value": "1", "effective_from_fy": "2082/83", "verified": "false"},
    ])
    assert table.verified("loan_loss_provision", "2081/82", "abc")[0].value == Decimal("1.10")
    try:
        table.verified("loan_loss_provision", "2082/83", "abc")
        raise AssertionError("must refuse")
    except ParametersUnavailable as exc:
        assert "python -m" not in str(exc)  # message is meant for the public


def test_open_ended_rows_count_up_to_the_current_year(tmp_path: Path, monkeypatch) -> None:
    import app.fiscal_year as fy

    monkeypatch.setattr(fy, "current_fy_start", lambda today=None: 2085)
    table = _write(tmp_path / "p.csv", [{"parameter": "cash_transaction_limit", "category": "single_transaction",
                                         "value": "500000", "family": "general", "effective_from_fy": "2082/83",
                                         "verified": "true"}])
    assert "2085/86" in table.fiscal_years()["cash_limit"]


# ── rate limiter: one entry per client IP was kept forever ───────────────────────────────────────────
def test_rate_limiter_forgets_idle_clients(monkeypatch) -> None:
    import app.main as main

    clock = [0.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    rl = RateLimiter(5)
    for i in range(RateLimiter.SWEEP_EVERY - 1):
        rl.allow(f"ip{i}")
    clock[0] = 120.0
    rl.allow("new")  # triggers the sweep
    assert len(rl._hits) == 1


# ── bank_parameters.csv: overdue bands had value "0", pages were the chunk's start page, no OCR flag ──
def test_overdue_band_rows_store_the_range_not_a_zero_value(tmp_path: Path) -> None:
    from ingest.review_parameters import overdue_bands
    from tools.calculators import ProvisionInput, calculate_loan_provision

    text = ("(१) असल (Pass) ः (अ) भाखा ननाघेका र १ महिनासम्म भाखा नाघेका\n"
            "(२) सूक्ष्म निगरानी (WatchList) ः (अ) १ महिनादेखि ३ महिनासम्म भाखा नाघेका\n"
            "(३) कमसल (Sub-standard) ः ३ महिनादेखि ६ महिनासम्म भाखा नाघेका\n"
            "(४) शंकास्पद (Doubtful) ः ६ महिनादेखि बढीमा १ वर्षसम्म भाखा नाघेका\n"
            "(५) खराब (Loss) ः १ वर्षभन्दा बढी अवधिले भाखा नाघेका\n")
    rows = overdue_bands({"doc_type": "directive", "text": text})
    got = {r["category"]: (r["value"], r["min_months"], r["max_months"]) for r in rows}
    assert got == {"pass": ("", "", "1"), "watchlist": ("", "1", "3"), "substandard": ("", "3", "6"),
                   "doubtful": ("", "6", "12"), "loss": ("", "12", "")}
    # the calculator works from the range; an empty value is not treated as a missing row
    base = {"family": "abc", "effective_from_fy": "2082/83", "verified": "true"}
    csv_rows = [{**base, "parameter": "loan_overdue_band", **r} for r in rows]
    csv_rows += [{**base, "parameter": "loan_loss_provision", "category": c, "value": v, "unit": "percent"}
                 for c, v in {"pass": "1", "watchlist": "5", "substandard": "25", "doubtful": "50", "loss": "100"}.items()]
    table = _write(tmp_path / "p.csv", csv_rows)
    loans = [{"amount": 100, "months_overdue": m} for m in (0, 1, 3, 6, 12, 13)]
    res = calculate_loan_provision(ProvisionInput(fiscal_year="2083/84", loans=loans), table)
    assert [r["category"] for r in res["rows"]] == ["pass", "pass", "watchlist", "substandard", "doubtful", "loss"]


def _locator(tmp_path: Path, pages: list[str], layer: dict[int, int], warnings: list[str] | None = None):
    from ingest.review_parameters import PageLocator

    ext = tmp_path / "extracted"
    ext.mkdir(exist_ok=True)
    (ext / "doc.json").write_text(json.dumps({"pages": [{"page": i + 1, "text": t} for i, t in enumerate(pages)],
                                              "warnings": warnings or []}, ensure_ascii=False), encoding="utf-8")
    loc = PageLocator(ext, tmp_path)
    loc._text_layer = lambda source_file, page: layer.get(page)  # type: ignore[method-assign]
    return loc


def test_page_is_where_the_quote_is_not_where_the_chunk_starts(tmp_path: Path) -> None:
    pages = ["cover letter of NRB", "(२) सूक्ष्म निगरानी १ महिनादेखि ३ महिनासम्म",
             "(३) कमसल (Sub-standard) ः ३ महिनादेखि ६ महिनासम्म भाखा"]
    loc = _locator(tmp_path, pages, {1: 500, 2: 500, 3: 500})
    # chunk starts on page 2, the substandard line is on page 3 (digits normalized: 3 == ३)
    assert loc.page_of("doc", "(3) कमसल (Sub-standard) ः 3 महिनादेखि 6 महिनासम्म भाखा", 2) == 3
    assert loc.page_of("doc", "(२) सूक्ष्म निगरानी 1 महिनादेखि", 1) == 2
    assert loc.page_of("doc", "text that is nowhere", 2) == 2  # not found: keep the chunk's page


def test_scanned_pages_are_marked_ocr(tmp_path: Path) -> None:
    loc = _locator(tmp_path, ["NRB cover letter", "...", "एक पटकमा पाँच लाख रुपेयाँ वा सोभन्दा बढी"], {1: 900, 2: 0, 3: 3})
    page = loc.page_of("doc", "एक पटकमा पाँच लाख रुपेयाँ वा सोभन्दा बढी", 1)
    assert page == 3 and loc.text_source("doc", "notice.pdf", page) == "ocr"
    assert loc.text_source("doc", "notice.pdf", 1) == "pdf_text"
    (tmp_path / "w").mkdir()
    whole = _locator(tmp_path / "w", ["x"], {1: 900}, ["whole document OCR'd: broken text layer"])
    assert whole.text_source("doc", "act.pdf", 1) == "ocr"


def test_reexport_keeps_verification_only_when_figure_range_and_quote_are_unchanged(tmp_path: Path, monkeypatch) -> None:
    import ingest.review_parameters as rp

    chunks = tmp_path / "chunks.json"
    chunks.write_text(json.dumps({"chunks": []}), encoding="utf-8")
    out = tmp_path / "p.csv"
    old = {"param_id": "a", "parameter": "loan_overdue_band", "category": "watchlist", "value": "0", "family": "abc",
           "effective_from_fy": "2082/83",
           "min_months": "1", "max_months": "3", "source_quote": "q", "verified": "true"}
    keep = {**old, "param_id": "b", "parameter": "loan_loss_provision", "value": "5", "min_months": "", "max_months": ""}
    _write(out, [old, keep])
    new_rows = [{**old, "value": "", "verified": "false"},  # band value changed: must be re-verified
                {**keep, "page": "8", "verified": "false"}]  # provision only moved page: stays verified
    monkeypatch.setattr(rp, "candidates", lambda c, locator=None: [dict(r) for r in new_rows])
    rp.export(chunks, out)
    import csv

    got = {r["param_id"]: r["verified"] for r in csv.DictReader(out.open(encoding="utf-8-sig"))}
    assert got == {"a": "false", "b": "true"}


# ── first question after start-up waited ~40 s for the embedding model ───────────────────────────────
def test_embedding_model_is_warmed_up_at_start(built_settings, monkeypatch) -> None:
    import app.main as main
    from app.store import IndexStore

    calls = []
    orig = IndexStore.embeddings.fget

    def spy(self):
        calls.append(1)
        return orig(self)

    monkeypatch.setattr(IndexStore, "embeddings", property(spy))
    monkeypatch.setattr(built_settings, "auto_rebuild_index", False)
    main.build_engine(built_settings)
    assert calls, "embedding model was not loaded at start-up"
