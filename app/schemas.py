"""Pydantic v2 models for the /chat contract.

This is the ONE place the wire contract is defined. See CLAUDE.md ->
"Non-negotiable API contract". Keep these models in exact sync with it.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

MAX_RECOMMENDATIONS = 10


class ChatMessage(BaseModel):
    # Tolerate extra/unknown fields on inbound messages (e.g. ids, timestamps).
    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # Tolerate extra/unknown top-level fields without erroring.
    model_config = ConfigDict(extra="ignore")

    messages: list[ChatMessage]


class Recommendation(BaseModel):
    name: str
    url: str
    # Comma-joined letters, e.g. "K" or "K,S" or "P,C".
    test_type: str


class ChatResponse(BaseModel):
    reply: str
    # Convention (matches the official traces): null when NOT recommending; a list of
    # 1-10 items when committed. An empty list is coerced to null; >10 is clamped.
    recommendations: Optional[list[Recommendation]] = Field(default=None)
    end_of_conversation: bool = False

    @model_validator(mode="after")
    def _normalize_recommendations(self) -> "ChatResponse":
        recs = self.recommendations
        if recs is not None:
            if len(recs) == 0:
                self.recommendations = None
            elif len(recs) > MAX_RECOMMENDATIONS:
                self.recommendations = recs[:MAX_RECOMMENDATIONS]
        return self


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
