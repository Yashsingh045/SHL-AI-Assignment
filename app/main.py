"""FastAPI entrypoint.

Implements the contract in CLAUDE.md: GET /health and POST /chat.

Robustness guarantees (engineering rules):
- Catalog + retrieval index (and the embedding model) are loaded ONCE at startup,
  never per request.
- /chat NEVER 500s and NEVER returns a schema-invalid body. Every failure path —
  malformed JSON, request-validation errors, or exceptions inside the agent —
  returns a valid ChatResponse fallback with HTTP 200.
- Long histories are truncated to ~6000 tokens (first user message + most recent
  turns) before hitting the agent/LLM.
"""
from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import catalog  # noqa: F401 - import loads the catalog at startup
from app.agent import handle_chat
from app.schemas import ChatRequest, ChatResponse, HealthResponse

MAX_HISTORY_TOKENS = 6000

_FALLBACK = ChatResponse(
    reply="Sorry — could you rephrase that?",
    recommendations=None,
    end_of_conversation=False,
)


def _fallback_response() -> JSONResponse:
    return JSONResponse(status_code=200, content=_FALLBACK.model_dump())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the retrieval index + embedding model so the first request is fast.
    # Best-effort: never block startup on it (SHL_WARMUP=0 skips it, e.g. in tests).
    if os.getenv("SHL_WARMUP", "1") != "0":
        try:
            from app import retrieval

            retrieval.warmup()
        except Exception:  # pragma: no cover - startup must not crash
            pass
    yield


app = FastAPI(title="SHL Assessment Recommender", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# History truncation
# --------------------------------------------------------------------------- #
def _est_tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4) + 1  # ~4 chars/token heuristic


def _truncate_history(messages: list, max_tokens: int = MAX_HISTORY_TOKENS) -> list:
    """Keep the FIRST user message (anchors the request) + the most recent turns,
    trimming older middle turns so the total stays under ~max_tokens."""
    if not messages:
        return messages
    if sum(_est_tokens(m.content) for m in messages) <= max_tokens:
        return messages

    first_user = next((i for i, m in enumerate(messages) if m.role == "user"), None)

    keep: set[int] = set()
    budget = max_tokens
    if first_user is not None:
        keep.add(first_user)
        budget -= _est_tokens(messages[first_user].content)

    # Walk from the newest message backwards, keeping turns until the budget runs out.
    for i in range(len(messages) - 1, -1, -1):
        if i in keep:
            continue
        cost = _est_tokens(messages[i].content)
        if budget - cost < 0:
            break
        keep.add(i)
        budget -= cost

    return [messages[i] for i in sorted(keep)]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    try:
        messages = _truncate_history(req.messages)
        return handle_chat(messages)
    except Exception:  # noqa: BLE001 - contract: /chat must never 500
        return _FALLBACK


# --------------------------------------------------------------------------- #
# Never surface 4xx/5xx errors on /chat — always return a valid ChatResponse body.
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/chat":
        return _fallback_response()
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    if request.url.path == "/chat":
        return _fallback_response()
    raise exc
