"""Pydantic v2 models for the /chat contract.

This is the ONE place the wire contract is defined. See CLAUDE.md ->
"Non-negotiable API contract". Keep these models in exact sync with it.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Category -> test_type letter. See CLAUDE.md letter map.
KEY_TO_LETTER: dict[str, str] = {
    "Ability & Aptitude": "A",
    "Biodata & Situational Judgment": "B",
    "Competencies": "C",
    "Development & 360": "D",
    "Assessment Exercises": "E",
    "Knowledge & Skills": "K",
    "Personality & Behavior": "P",
    "Simulations": "S",
}


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


class Recommendation(BaseModel):
    name: str
    url: str
    # Comma-joined letters, e.g. "K" or "K,S" or "P,C".
    test_type: str


class ChatResponse(BaseModel):
    reply: str
    # null OR array of 1-10 items (never an empty list — use None instead).
    recommendations: Optional[list[Recommendation]] = Field(default=None)
    end_of_conversation: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
