"""
config.py
=========
Central configuration loaded from environment variables / `.env` via pydantic-settings.

Every tunable (models, weights, limits, paths) lives here so that no module
hard-codes a value that may need to change. Regulatory figures (provisioning rates,
limits) and the current fiscal year are deliberately NOT configuration: figures come
from the human-verified `bank_parameters.csv`, and the fiscal year is computed from today's date.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings. Field names map to upper-case env variables."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── API keys ──────────────────────────────────────────────────────────────
    google_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Models ────────────────────────────────────────────────────────────────
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_temperature: float = 0.2
    claude_model: str = "claude-sonnet-5-5"
    # One-time enrichment provider: "gemini" (free tier), "ollama" (local, free),
    # "claude" (paid) or "none" (deterministic heuristics only).
    # The NRB corpus has ~7,300 chunks; the free Gemini tier needs several days for that, so the
    # default build uses the free deterministic heuristics + bilingual glossary.
    enrich_provider: str = "none"
    enrich_gemini_model: str = "gemini-3.5-flash-lite"
    enrich_requests_per_minute: int = 10
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_batch_size: int = 32
    enable_rerank: bool = False
    rerank_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    # ── Retrieval ─────────────────────────────────────────────────────────────
    bm25_weight: float = 0.5
    vector_weight: float = 0.5
    candidates_per_retriever: int = 20
    top_k: int = 6
    rrf_k: int = 60
    max_context_chars: int = 18000
    # Rulebook preferred when a question does not name an institution type:
    # abc = class A/B/C banks (commercial/development banks, finance companies). "" = no preference.
    default_family: str = "abc"

    # ── Chunking ──────────────────────────────────────────────────────────────
    max_section_chars: int = 3000
    target_sub_chunk_chars: int = 1800

    # ── Conversation memory ───────────────────────────────────────────────────
    memory_turns: int = 6
    max_sessions: int = 2000
    session_ttl_minutes: int = 120

    # ── HTTP / safety ─────────────────────────────────────────────────────────
    allowed_origins: str = "http://localhost:5173"
    max_message_chars: int = 2000
    rate_limit_per_minute: int = 30
    log_level: str = "INFO"
    auto_rebuild_index: bool = True
    warm_up_embeddings: bool = True  # load the embedding model at start-up instead of on the first question

    # ── Paths (relative paths are resolved against backend/) ────────────────
    data_dir: Path = Field(default=Path("data"))

    @property
    def origins(self) -> list[str]:
        """ALLOWED_ORIGINS as a list (comma separated in the env)."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        """Absolute data directory."""
        p = self.data_dir
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def raw_dir(self) -> Path:
        return self.data_path / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.raw_dir / "manifest.yaml"

    @property
    def processed_dir(self) -> Path:
        return self.data_path / "processed"

    @property
    def index_dir(self) -> Path:
        return self.data_path / "index"

    @property
    def structural_chunks_path(self) -> Path:
        return self.processed_dir / "structural_chunks.json"

    @property
    def enriched_chunks_path(self) -> Path:
        return self.processed_dir / "enriched_chunks.jsonl"

    @property
    def parameters_path(self) -> Path:
        return self.processed_dir / "bank_parameters.csv"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (tests may call `get_settings.cache_clear()`)."""
    return Settings()
