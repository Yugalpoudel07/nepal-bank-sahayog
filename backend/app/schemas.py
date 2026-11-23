"""
schemas.py
==========
Request/response models for the HTTP API.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.fiscal_year import normalize_fy_label


class ChatRequest(BaseModel):
    """POST /chat and /chat/stream body."""

    message: str = Field(min_length=1)
    session_id: str = Field(default="default", pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    fiscal_year: str | None = Field(default=None, description='e.g. "2083/84"')
    language: Literal["en", "ne"] | None = None

    @field_validator("message")
    @classmethod
    def _limit(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty.")
        limit = get_settings().max_message_chars
        if len(v) > limit:
            raise ValueError(f"Message is too long (max {limit} characters).")
        return v

    @field_validator("fiscal_year")
    @classmethod
    def _fy(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        label = normalize_fy_label(v)
        if label is None:
            raise ValueError('fiscal_year must look like "2083/84".')
        return label


class Source(BaseModel):
    id: str
    chunk_id: str
    title: str
    doc_label: str | None = None
    doc_type: str
    institutions: list[str] = []
    circular_no: str | None = None
    section: str | None
    fy: str | None
    effective_to_fy: str | None
    page: int | None
    snippet: str
    source_url: str | None
    status: str
    cite: str


class ChatResponse(BaseModel):
    answer: str
    fiscal_year_used: str
    fiscal_year_requested: str
    fy_source: str
    fy_warning: str | None = None
    language: str
    sources: list[Source]
    calculation: dict | None = None


class FiscalYearsResponse(BaseModel):
    current: str
    default: str
    available: list[str]
    calculator_years: dict[str, list[str]]


class DocumentInfo(BaseModel):
    doc_id: str
    title: str
    doc_label: str | None = None
    family: str | None = None
    doc_type: str
    language: str
    fiscal_year: str | None
    issue_date_bs: str | None
    source_url: str | None
    path: str
    chunks: int
