"""FastAPI entrypoint.

Wires up the contract in CLAUDE.md: GET /health and POST /chat. /chat delegates to
app.agent.handle_chat, which is stateless and always returns a schema-valid response
(never 500s) per the engineering rules.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.agent import handle_chat
from app.schemas import ChatRequest, ChatResponse, HealthResponse

app = FastAPI(title="SHL Assessment Recommender")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return handle_chat(req.messages)
