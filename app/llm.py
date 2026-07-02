"""LLM client with provider fallback.

STUB — to be implemented. See CLAUDE.md engineering rules.

Responsibilities:
- Primary: Groq (llama-3.3-70b-versatile). Fallback: Gemini Flash.
  Keys from env: GROQ_API_KEY / GEMINI_API_KEY.
- JSON mode where possible, temperature 0-0.2.
- Strict try/except: on any failure return a safe fallback so /chat never 500s.
- Keep total usage to <= 2 LLM calls per /chat request.
"""
from __future__ import annotations

# TODO(agent-task): implement Groq-primary / Gemini-fallback JSON completion helper.
