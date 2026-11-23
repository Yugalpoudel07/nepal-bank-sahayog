"""
rag_engine.py
=============
The question-answering engine.

answer(question, session, FY) does:
  1. load short conversation memory; rewrite follow-ups ("and for VAT?") into a standalone question;
  2. resolve the fiscal year: question > UI selection > current FY; fall back to the latest
     FY with documents (with a visible warning) when none exist for the requested year;
  3. retrieve FY-valid context (direct directive/section lookup + hybrid search + edition overrides +
     amending circulars);
  4. let the LLM decide whether to call a deterministic calculator (function calling);
  5. generate the answer from the context only, then guarantee the FY line and disclaimer;
  6. return answer + FY used + warning + sources + calculation.
A streaming variant yields the same information as server-sent events.

Nothing here knows a regulatory figure: calculators read the human-verified bank_parameters.csv.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field

from app.config import Settings
from app.fiscal_year import current_fy_start, fy_label, parse_fy
from app.fy_detect import detect_fy
from app.llm import ChatMessage, LLMClient, LLMUnavailable
from app.memory import ConversationMemory, Turn
from app.prompts import LANGUAGE_RULES, REWRITE_PROMPT, SYSTEM_PROMPT, TEXTS, cite_label, format_context
from app.retrieval import Hit, Retriever
from app.section_detect import detect_sections
from app.store import IndexStore
from app.textproc import is_nepali
from tools.calculators import NO_CALCULATION, TOOL_SPECS, run_tool
from tools.parameters import ParameterTable

log = logging.getLogger(__name__)

_FOLLOW_UP_RE = re.compile(
    r"^\s*(and|also|what about|how about|then|same|for|र\s|अनि|त्यसो|त्यसैगरी|ani\b|ra\b)", re.IGNORECASE
)
_CALC_INTENT_RE = re.compile(
    r"calculat|compute|how much|provision|overdue|cash|in cash|limit|कति|हिसाब|नोक्सानी|भाखा|नगद|सीमा|"
    r"kati|hisab|noksani|bhakha|nagad", re.IGNORECASE
)
_AMOUNT_RE = re.compile(r"\d[\d,]{2,}|[०-९][०-९,]{2,}|\blakh|\bcrore|लाख|करोड", re.IGNORECASE)
_ROMANIZED_NE_RE = re.compile(
    r"\b(kasari|garne|kati|ho|cha|chha|lagcha|lagchha|kun|kina|tirnu|darta|bhanne|parcha|huncha|karja|byaj|"
    r"khata|nikshep|bachat|paisa|rakam|sakchha|sakincha|milcha|paincha|chahincha)\b",
    re.IGNORECASE,
)
MAX_TOOL_CALLS = 2
HEAD_CHARS = 200   # streamed answers: characters buffered before a stray FY heading is removed
TAIL_CHARS = 300   # streamed answers: characters held back until a stray disclaimer can be removed
_FY_LINE_RE = re.compile(r"\s*\**\s*(applies\s+to\s+fy|लागू\s+हुने\s+आर्थिक\s+वर्ष)[^\n]*\n*", re.IGNORECASE)
_DISCLAIMER_RE = re.compile(
    r"(\s*[-—*_]*\s*\**_*\s*(general information from nrb documents|यो\s+NRB\s+कागजातमा\s+आधारित)[^\n]*)+\s*$",
    re.IGNORECASE,
)
# Gemini sometimes writes Devanagari figures with look-alike digits of other scripts (Khmer, Arabic-Indic, Bengali).
_FOREIGN_DIGITS = {ord(ch): d for base in (0x17E0, 0x0660, 0x06F0, 0x09E6)
                   for ch, d in zip(map(chr, range(base, base + 10)), "०१२३४५६७८९")}


def fix_digits(text: str) -> str:
    """Replace digits of unrelated scripts with Devanagari digits."""
    return text.translate(_FOREIGN_DIGITS)


@dataclass
class FYResolution:
    used: str
    requested: str
    source: str  # question | ui | current
    warning: str | None = None


@dataclass
class ChatResult:
    answer: str
    fiscal_year_used: str
    fiscal_year_requested: str
    fy_source: str
    fy_warning: str | None
    language: str
    sources: list[dict] = field(default_factory=list)
    calculation: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Prepared:
    question: str
    standalone: str
    language: str
    fy: FYResolution
    hits: list[Hit]
    sources: list[dict]
    messages: list[ChatMessage]
    offer_tools: bool


def detect_language(text: str) -> str:
    """'ne' for Devanagari or romanized Nepali, else 'en'."""
    if is_nepali(text):
        return "ne"
    return "ne" if len(_ROMANIZED_NE_RE.findall(text)) >= 2 else "en"


class BankRAGEngine:
    """Coordinates memory, FY resolution, retrieval, calculators and the LLM."""

    def __init__(self, settings: Settings, store: IndexStore, llm: LLMClient | None,
                 params: ParameterTable, memory: ConversationMemory | None = None) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.params = params
        self.retriever = Retriever(store, settings)
        self.memory = memory or ConversationMemory(settings.memory_turns, settings.max_sessions,
                                                   settings.session_ttl_minutes)

    # ── fiscal year ───────────────────────────────────────────────────────────
    def resolve_fy(self, question: str, ui_fy: str | None, language: str) -> FYResolution:
        detected = detect_fy(question)
        if detected:
            requested, source = detected.start, "question"
        elif parse_fy(ui_fy):
            requested, source = parse_fy(ui_fy), "ui"  # type: ignore[assignment]
        else:
            requested, source = current_fy_start(), "current"
        available = [parse_fy(f) for f in self.store.available_fys()]
        t = TEXTS[language]
        if not available:
            return FYResolution(fy_label(requested), fy_label(requested), source, t["fy_no_docs"])
        if requested in available:
            return FYResolution(fy_label(requested), fy_label(requested), source)
        used = max(a for a in available if a is not None)
        warning = t["fy_missing"].format(requested=fy_label(requested), used=fy_label(used))
        return FYResolution(fy_label(used), fy_label(requested), source, warning)

    # ── preparation ───────────────────────────────────────────────────────────
    def _rewrite(self, question: str, history: list[Turn]) -> str:
        if not history or self.llm is None:
            return question
        short = len(question.split()) <= 8
        if not (short or _FOLLOW_UP_RE.match(question)):
            return question
        convo = "\n".join(f"User: {t.question}\nAssistant: {t.answer[:400]}" for t in history[-3:])
        try:
            reply = self.llm.complete([ChatMessage("user", REWRITE_PROMPT.format(history=convo, question=question))])
            rewritten = reply.text.strip().strip('"')
            return rewritten if 0 < len(rewritten) < 600 else question
        except LLMUnavailable:
            return f"{history[-1].question} {question}"

    def _sources(self, hits: list[Hit]) -> list[dict]:
        out = []
        for i, h in enumerate(hits, 1):
            c = h.chunk
            section = None
            no, kind = c.get("section_no"), c.get("unit_kind")
            if c["doc_type"] == "directive" and kind == "section" and no:
                d, _, pt = no.partition(".")
                section = f"Directive {d}" + (f": {c['chapter_title']}" if c.get("chapter_title") else "")
                if pt and pt != "0":
                    section += f" › Point {no}" + (f": {c['section_title']}" if c.get("section_title") else "")
            elif kind == "schedule":
                section = (c.get("section_title") or f"Annex {no}")
            elif kind == "section" and not no and c.get("section_title"):
                section = c["section_title"]
            elif no:
                section = f"{'Rule' if c['doc_type'] == 'rules' else 'Section'} {no}" + (
                    f": {c['section_title']}" if c.get("section_title") else "")
            snippet = re.sub(r"\s+", " ", c["text"]).strip()
            out.append({
                "id": f"S{i}",
                "chunk_id": c["chunk_id"],
                "title": c["title"],
                "doc_label": c.get("doc_label") or c["title"],
                "doc_type": c["doc_type"],
                "institutions": c.get("institutions") or [],
                "circular_no": c.get("circular_no"),
                "section": section,
                "fy": c.get("effective_from_fy") or c.get("fiscal_year"),
                "effective_to_fy": c.get("effective_to_fy"),
                "page": c.get("page"),
                "snippet": snippet[:280] + ("…" if len(snippet) > 280 else ""),
                "source_url": c.get("source_url"),
                "status": h.status,
                "cite": cite_label(c),
            })
        return out

    def _prepare(self, question: str, session_id: str, ui_fy: str | None, language: str | None) -> _Prepared:
        history = self.memory.get(session_id)
        lang = language or detect_language(question)
        standalone = self._rewrite(question, history)
        fy_q = question if detect_fy(question) else standalone
        fy = self.resolve_fy(fy_q, ui_fy, lang)
        fy_start = parse_fy(fy.used)
        refs = detect_sections(question) or detect_sections(standalone)
        hits = self.retriever.retrieve(standalone, fy_start, refs)  # type: ignore[arg-type]

        texts = {h.id: self.store.parent_text(h.chunk, self.settings.max_section_chars) for h in hits}
        context = format_context(hits, fy.used, texts)[: self.settings.max_context_chars]
        system = SYSTEM_PROMPT.format(fy=fy.used, language_rule=LANGUAGE_RULES[lang])
        messages = [ChatMessage("system", system)]
        for t in history:
            messages += [ChatMessage("user", t.question), ChatMessage("assistant", t.answer)]
        user = f"CONTEXT (fiscal year {fy.used}):\n\n{context}\n\nQUESTION: {question}"
        if standalone != question:
            user += f"\n(Interpreted as: {standalone})"
        messages.append(ChatMessage("user", user))
        offer_tools = bool(_CALC_INTENT_RE.search(question) and _AMOUNT_RE.search(question))
        return _Prepared(question, standalone, lang, fy, hits, self._sources(hits), messages, offer_tools)

    # ── calculators ───────────────────────────────────────────────────────────
    def _maybe_calculate(self, p: _Prepared) -> dict | None:
        """
        Make the LLM choose a calculator (or NO_CALCULATION) for a question with an amount and a calculation
        intent, so it can never do the arithmetic itself. Returns the calculator result (None for
        NO_CALCULATION); the answer is written afterwards from it and the context.
        """
        assert self.llm is not None
        reply = self.llm.complete(p.messages, tools=TOOL_SPECS, force_tool=True)
        calls = [c for c in reply.tool_calls if c.name != NO_CALCULATION][:MAX_TOOL_CALLS]
        if not calls:
            return None
        calculation = None
        p.messages.append(ChatMessage("assistant", reply.text, tool_calls=calls))
        for call in calls:
            result = run_tool(call.name, call.args, p.fy.used, self.params)
            calculation = calculation or result
            p.messages.append(ChatMessage("tool", json.dumps(result, ensure_ascii=False), tool_call_id=call.id))
        return calculation

    # ── post-processing ───────────────────────────────────────────────────────
    @staticmethod
    def _clean_head(text: str) -> str:
        """Drop fiscal-year heading lines the model wrote itself (either language) from the top."""
        while (m := _FY_LINE_RE.match(text)) and m.end() > 0:
            text = text[m.end():]
        return text

    @staticmethod
    def _clean_tail(text: str) -> str:
        """Drop disclaimer lines the model wrote itself (either language) from the end."""
        return _DISCLAIMER_RE.sub("", text).rstrip()

    def _finalize(self, text: str, p: _Prepared) -> str:
        """Exactly one FY line (in the reply language) on top, exactly one disclaimer at the end."""
        t = TEXTS[p.language]
        body = self._clean_tail(self._clean_head(fix_digits(text).strip())).strip()
        return f"{t['applies'].format(fy=p.fy.used)}\n\n{body}\n\n{t['disclaimer']}"

    def _not_found(self, p: _Prepared) -> str:
        t = TEXTS[p.language]
        return f"{t['applies'].format(fy=p.fy.used)}\n\n{t['not_found'].format(fy=p.fy.used)}\n\n{t['disclaimer']}"

    def _result(self, p: _Prepared, answer: str, calculation: dict | None) -> ChatResult:
        return ChatResult(
            answer=answer, fiscal_year_used=p.fy.used, fiscal_year_requested=p.fy.requested, fy_source=p.fy.source,
            fy_warning=p.fy.warning, language=p.language, sources=p.sources, calculation=calculation,
        )

    def _remember(self, session_id: str, p: _Prepared, answer: str) -> None:
        self.memory.add(session_id, Turn(p.question, answer, p.fy.used))

    def _require_llm(self) -> LLMClient:
        if self.llm is None:
            raise LLMUnavailable("The language model is not configured (set GOOGLE_API_KEY in backend/.env).")
        return self.llm

    # ── public API ────────────────────────────────────────────────────────────
    def answer(self, question: str, session_id: str, fiscal_year: str | None = None,
               language: str | None = None) -> ChatResult:
        llm = self._require_llm()
        p = self._prepare(question, session_id, fiscal_year, language)
        if not p.hits:
            text = self._not_found(p)
            self._remember(session_id, p, text)
            return self._result(p, text, None)
        calculation = self._maybe_calculate(p) if p.offer_tools else None
        text = llm.complete(p.messages).text
        text = self._finalize(text, p)
        self._remember(session_id, p, text)
        log.info("answered", extra={"kv": {"fy": p.fy.used, "sources": len(p.sources), "calc": bool(calculation)}})
        return self._result(p, text, calculation)

    def answer_stream(self, question: str, session_id: str, fiscal_year: str | None = None,
                      language: str | None = None) -> Iterator[tuple[str, dict]]:
        """Yield (event, data): meta, token*, done. Errors surface as LLMUnavailable."""
        llm = self._require_llm()
        p = self._prepare(question, session_id, fiscal_year, language)
        meta = {"fiscal_year_used": p.fy.used, "fiscal_year_requested": p.fy.requested, "fy_source": p.fy.source,
                "fy_warning": p.fy.warning, "language": p.language, "sources": p.sources}
        if not p.hits:
            text = self._not_found(p)
            yield "meta", meta
            yield "token", {"text": text}
            self._remember(session_id, p, text)
            yield "done", self._result(p, text, None).to_dict()
            return

        calculation = self._maybe_calculate(p) if p.offer_tools else None
        yield "meta", {**meta, "calculation": calculation}

        source: Iterator[str] = llm.stream(p.messages)
        # The head is buffered until a stray FY heading can be removed, the last TAIL_CHARS until a stray
        # disclaimer can be removed; everything in between streams immediately.
        t = TEXTS[p.language]
        yield "token", {"text": t["applies"].format(fy=p.fy.used) + "\n\n"}
        sent: list[str] = [t["applies"].format(fy=p.fy.used) + "\n\n"]
        buf = ""
        head_done = False
        for piece in source:
            buf += fix_digits(piece)
            if not head_done:
                if len(buf) < HEAD_CHARS:
                    continue
                buf, head_done = self._clean_head(buf.lstrip()), True
            if len(buf) > TAIL_CHARS:
                out, buf = buf[:-TAIL_CHARS], buf[-TAIL_CHARS:]
                sent.append(out)
                yield "token", {"text": out}
        if not head_done:
            buf = self._clean_head(buf.lstrip())
        end = self._clean_tail(buf) + "\n\n" + t["disclaimer"]
        sent.append(end)
        yield "token", {"text": end}
        final = "".join(sent).strip()  # exactly what the client received
        self._remember(session_id, p, final)
        yield "done", self._result(p, final, calculation).to_dict()
