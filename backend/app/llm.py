"""
llm.py
======
Runtime LLM behind a small provider-neutral interface.

`LLMClient` exposes two calls:
    complete(messages, tools=None) -> LLMReply  (text and/or tool calls)
    stream(messages)               -> iterator of text deltas

`GeminiClient` implements it with langchain-google-genai (as in the reference
project). To swap providers, implement `LLMClient` and return it from `get_llm`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import Settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """The language model is not configured or the provider call failed."""


QUOTA_MESSAGE = ("The free AI quota is used up for the moment. Please try again in a minute (or tomorrow if the "
                 "daily limit is reached). / निःशुल्क AI सीमा अहिलेलाई सकिएको छ। केही समयपछि फेरि प्रयास गर्नुहोस्।")
BUSY_MESSAGE = ("The AI service is not responding right now. Please try again shortly. / "
                "AI सेवाले अहिले जवाफ दिएन। केही बेरपछि फेरि प्रयास गर्नुहोस्।")
LLM_MAX_RETRIES = 1     # free tier: fail fast with a clear message instead of retrying for a minute
LLM_TIMEOUT_S = 60


def _friendly(exc: Exception) -> LLMUnavailable:
    """Map a provider error to a message an end user can understand (details stay in the server log)."""
    text = f"{type(exc).__name__} {exc}".lower()
    if any(k in text for k in ("429", "resource_exhausted", "resourceexhausted", "quota", "rate limit")):
        return LLMUnavailable(QUOTA_MESSAGE)
    return LLMUnavailable(BUSY_MESSAGE)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class LLMReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(ABC):
    """Provider-neutral chat interface."""

    @abstractmethod
    def complete(self, messages: list[ChatMessage], tools: list[dict] | None = None,
                 force_tool: bool = False) -> LLMReply:
        """Single completion; may return tool calls when tools are offered (must call one when force_tool)."""

    @abstractmethod
    def stream(self, messages: list[ChatMessage]) -> Iterator[str]:
        """Stream the answer text."""


def _content_text(content: Any) -> str:
    """LangChain message content may be a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text", ""))
        return "".join(out)
    return ""


class GeminiClient(LLMClient):
    """Google Gemini through langchain-google-genai."""

    def __init__(self, settings: Settings) -> None:
        if not settings.google_api_key:
            raise LLMUnavailable("GOOGLE_API_KEY is not set in backend/.env")
        from langchain_google_genai import ChatGoogleGenerativeAI

        self._llm = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            temperature=settings.gemini_temperature,
            google_api_key=settings.google_api_key,
            max_retries=LLM_MAX_RETRIES,
            timeout=LLM_TIMEOUT_S,
        )

    @staticmethod
    def _to_langchain(messages: list[ChatMessage]) -> list[Any]:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        out: list[Any] = []
        for m in messages:
            if m.role == "system":
                out.append(SystemMessage(content=m.content))
            elif m.role == "user":
                out.append(HumanMessage(content=m.content))
            elif m.role == "assistant":
                out.append(AIMessage(
                    content=m.content,
                    tool_calls=[{"id": t.id, "name": t.name, "args": t.args} for t in m.tool_calls],
                ))
            else:
                out.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
        return out

    def complete(self, messages: list[ChatMessage], tools: list[dict] | None = None,
                 force_tool: bool = False) -> LLMReply:
        llm = self._llm
        if tools:
            llm = llm.bind_tools([{"type": "function", "function": t} for t in tools],
                                 tool_choice="any" if force_tool else None)
        try:
            msg = llm.invoke(self._to_langchain(messages))
        except Exception as exc:
            log.exception("gemini call failed")
            raise _friendly(exc) from exc
        calls = [
            ToolCall(id=c.get("id") or f"call_{i}", name=c["name"], args=dict(c.get("args") or {}))
            for i, c in enumerate(getattr(msg, "tool_calls", None) or [])
        ]
        return LLMReply(text=_content_text(msg.content), tool_calls=calls)

    def stream(self, messages: list[ChatMessage]) -> Iterator[str]:
        try:
            for chunk in self._llm.stream(self._to_langchain(messages)):
                text = _content_text(chunk.content)
                if text:
                    yield text
        except Exception as exc:
            log.exception("gemini stream failed")
            raise _friendly(exc) from exc


def get_llm(settings: Settings) -> LLMClient:
    """Factory for the configured runtime LLM."""
    return GeminiClient(settings)
