"""
main.py
=======
FastAPI server for Nepal Bank Sahayak.

Endpoints
  GET  /health                 liveness + index/LLM status
  GET  /fiscal-years           available FYs (from indexed documents) + current FY
  POST /chat                   {message, session_id, fiscal_year?, language?} -> answer + sources
  POST /chat/stream            same, as Server-Sent Events (meta, token..., done | error)
  POST /calculate/loan-provision   deterministic calculator (verified figures only)
  POST /calculate/cash-limit
  GET  /documents              indexed documents with their FYs

Run (from backend/):
  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.fiscal_year import current_fy
from app.llm import LLMUnavailable, get_llm
from app.logging_setup import setup_logging
from app.rag_engine import BankRAGEngine
from app.schemas import ChatRequest, ChatResponse, DocumentInfo, FiscalYearsResponse
from app.store import IndexStore
from tools.calculators import CashInput, ProvisionInput, calculate_loan_provision, check_cash_transaction
from tools.parameters import ParametersUnavailable, ParameterTable

log = logging.getLogger("api")

RATE_LIMITED_PREFIXES = ("/chat", "/calculate")


class RateLimiter:
    """Sliding one-minute window per client IP (in-process; use a gateway for multi-worker deployments)."""

    SWEEP_EVERY = 1000  # requests between removals of idle clients, so the table cannot grow without bound

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._calls = 0

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        self._calls += 1
        if self._calls % self.SWEEP_EVERY == 0:
            for k in [k for k, q in self._hits.items() if not q or now - q[-1] > 60]:
                del self._hits[k]
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def build_engine(settings: Settings) -> BankRAGEngine:
    """Ensure the index is current, then wire store, LLM and calculators together."""
    if settings.auto_rebuild_index:
        from ingest.build_index import ensure_index

        ensure_index(settings)
    store = IndexStore(settings)
    if settings.warm_up_embeddings:
        # Load the embedding model now, so the first user question does not wait ~40 s for it.
        try:
            store.embeddings.embed_query("warm up")
        except Exception as exc:  # the server still starts; the model loads on the first question
            log.warning("embedding warm-up failed", extra={"kv": {"error": str(exc)}})
    try:
        llm = get_llm(settings)
    except LLMUnavailable as exc:
        log.warning("LLM not available; /chat will return 503", extra={"kv": {"reason": str(exc)}})
        llm = None
    return BankRAGEngine(settings, store, llm, ParameterTable(settings.parameters_path))


def create_app(engine: BankRAGEngine | None = None, settings: Settings | None = None) -> FastAPI:
    """Application factory (tests inject an engine with a fake LLM)."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine
        app.state.startup_error = None
        if app.state.engine is None:
            try:
                app.state.engine = await run_in_threadpool(build_engine, settings)
            except Exception as exc:  # keep serving /health with a clear reason
                log.exception("engine failed to start")
                app.state.startup_error = str(exc)
        yield

    app = FastAPI(title="Nepal Bank Sahayak API", version="1.0.0",
                  description="Fiscal-year-aware RAG assistant for Nepal Rastra Bank rules", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.origins, allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"],
    )
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def rate_limit_and_log(request: Request, call_next):
        started = time.perf_counter()
        if request.method == "POST" and request.url.path.startswith(RATE_LIMITED_PREFIXES):
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                return JSONResponse({"detail": "Too many requests. Please wait a minute and try again."},
                                    status_code=429)
        response = await call_next(request)
        log.info("request", extra={"kv": {"method": request.method, "path": request.url.path,
                                          "status": response.status_code,
                                          "ms": round((time.perf_counter() - started) * 1000)}})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        msgs = [str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors()]
        return JSONResponse({"detail": "; ".join(msgs) or "Invalid request."}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "Internal server error. Check the server logs."}, status_code=500)

    def get_engine(request: Request) -> BankRAGEngine:
        eng = getattr(request.app.state, "engine", None)
        if eng is None:
            reason = getattr(request.app.state, "startup_error", None) or "Engine not ready."
            raise HTTPException(status_code=503, detail=reason)
        return eng

    @app.get("/health", tags=["Health"])
    def health(request: Request) -> dict:
        eng = getattr(request.app.state, "engine", None)
        return {
            "status": "ok" if eng else "degraded",
            "service": "Nepal Bank Sahayak API",
            "index_loaded": eng is not None,
            "llm_configured": bool(eng and eng.llm),
            "chunks": len(eng.store.chunks) if eng else 0,
            "error": getattr(request.app.state, "startup_error", None),
        }

    @app.get("/fiscal-years", response_model=FiscalYearsResponse, tags=["Meta"])
    def fiscal_years(eng: BankRAGEngine = Depends(get_engine)) -> FiscalYearsResponse:
        cur = current_fy()
        return FiscalYearsResponse(current=cur, default=cur, available=eng.store.available_fys(),
                                   calculator_years=eng.params.fiscal_years())

    @app.get("/documents", response_model=list[DocumentInfo], tags=["Meta"])
    def documents(eng: BankRAGEngine = Depends(get_engine)) -> list[dict]:
        return eng.store.document_list()

    @app.post("/chat", response_model=ChatResponse, tags=["Chat"])
    async def chat(req: ChatRequest, eng: BankRAGEngine = Depends(get_engine)) -> dict:
        try:
            result = await run_in_threadpool(eng.answer, req.message, req.session_id, req.fiscal_year, req.language)
        except LLMUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/chat/stream", tags=["Chat"])
    def chat_stream(req: ChatRequest, eng: BankRAGEngine = Depends(get_engine)) -> StreamingResponse:
        if eng.llm is None:
            raise HTTPException(status_code=503, detail="The language model is not configured (GOOGLE_API_KEY).")

        def events() -> Iterator[str]:
            try:
                for event, data in eng.answer_stream(req.message, req.session_id, req.fiscal_year, req.language):
                    yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            except LLMUnavailable as exc:
                yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            except Exception:
                log.exception("stream failed")
                yield f"event: error\ndata: {json.dumps({'detail': 'Internal server error.'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/calculate/loan-provision", tags=["Calculators"])
    def loan_provision(data: ProvisionInput, eng: BankRAGEngine = Depends(get_engine)) -> dict:
        try:
            return calculate_loan_provision(data, eng.params)
        except ParametersUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/calculate/cash-limit", tags=["Calculators"])
    def cash_limit(data: CashInput, eng: BankRAGEngine = Depends(get_engine)) -> dict:
        try:
            return check_cash_transaction(data, eng.params)
        except ParametersUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
