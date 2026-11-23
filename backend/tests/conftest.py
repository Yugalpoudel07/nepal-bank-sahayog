"""
Shared fixtures: a complete index built from the SAMPLE corpus in tests/fixtures/raw into a temp
directory (hashing embeddings, no network, no API keys) and a fake LLM.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest

from app.config import BACKEND_DIR, Settings, get_settings
from app.llm import ChatMessage, LLMClient, LLMReply, ToolCall

SAMPLE_RAW = BACKEND_DIR / "tests" / "fixtures" / "raw"


class FakeLLM(LLMClient):
    """Deterministic LLM: echoes the first context citation; calls a calculator when tools are offered."""

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage], tools: list[dict] | None = None,
                 force_tool: bool = False) -> LLMReply:
        self.calls.append(messages)
        last = messages[-1]
        if tools and last.role == "user":
            q = last.content.lower().split("question:")[-1]
            if "cash" in q or "नगद" in q:
                return LLMReply("", [ToolCall("call_1", "check_cash_transaction", {"amount": 700000})])
            args = {"loans": [{"amount": 1000000, "months_overdue": 0}, {"amount": 200000, "months_overdue": 4}]}
            return LLMReply("", [ToolCall("call_1", "calculate_loan_provision", args)])
        if last.role == "user" and last.content.startswith("Rewrite the user's latest message"):
            return LLMReply("How much loan loss provision is needed for a substandard loan?")
        cite = ""
        for line in last.content.splitlines():
            if line.startswith("Cite as: "):
                cite = line.removeprefix("Cite as: ")
                break
        return LLMReply(f"Here is the answer {cite}.")

    def stream(self, messages: list[ChatMessage]) -> Iterator[str]:
        text = self.complete(messages).text
        for word in text.split(" "):
            yield word + " "


@pytest.fixture(scope="session")
def built_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    """Run the whole pipeline (without Claude) on a copy of the SAMPLE corpus."""
    data = tmp_path_factory.mktemp("data")
    shutil.copytree(SAMPLE_RAW, data / "raw", ignore=shutil.ignore_patterns("manifest.draft.yaml"))
    mp = pytest.MonkeyPatch()
    mp.setenv("DATA_DIR", str(data))
    mp.setenv("EMBEDDING_MODEL", "hashing")
    mp.setenv("ANTHROPIC_API_KEY", "")
    mp.setenv("GOOGLE_API_KEY", "")
    mp.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    get_settings.cache_clear()

    from ingest import run_all

    run_all.main(["--skip-enrich"])
    settings = get_settings()
    mark_all_verified(settings.parameters_path)
    yield settings
    mp.undo()
    get_settings.cache_clear()


def mark_all_verified(path) -> None:
    """Tests stand in for the human reviewer: every SAMPLE candidate row counts as verified."""
    import csv

    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["verified"] = "true"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture()
def engine(built_settings: Settings):
    from app.rag_engine import BankRAGEngine
    from app.store import IndexStore
    from tools.parameters import ParameterTable

    return BankRAGEngine(built_settings, IndexStore(built_settings), FakeLLM(),
                         ParameterTable(built_settings.parameters_path))


@pytest.fixture()
def client(engine, built_settings: Settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(engine=engine, settings=built_settings)) as c:
        yield c
