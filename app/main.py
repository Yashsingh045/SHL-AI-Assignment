"""FastAPI entrypoint.

STUB — wires up the endpoints defined by the contract in CLAUDE.md.
GET /health returns {"status": "ok"}. POST /chat is stubbed until the agent lands;
it already returns a schema-valid response (never 500s) per engineering rules.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.schemas import ChatRequest, ChatResponse, HealthResponse

app = FastAPI(title="SHL Assessment Recommender")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # TODO(agent-task): delegate to app.agent. Until then, return a safe,
    # schema-valid placeholder (recommendations=null) so the contract holds.
    return ChatResponse(
        reply="Service scaffolded; the recommender is not implemented yet.",
        recommendations=None,
        end_of_conversation=False,
    )
