"""Thin LLM client: Groq primary, Gemini Flash fallback.

Per CLAUDE.md engineering rules: JSON mode where possible, temperature 0.1, a single
automatic retry, then fall back to the other provider, then raise LLMError. Callers
(app.agent) are expected to catch LLMError and degrade to a safe response so /chat
never 500s. Total budget is <= 2 LLM calls per /chat request (enforced by the agent).

Keys via env: GROQ_API_KEY / GEMINI_API_KEY. Clients are created lazily so importing
this module never requires keys (tests monkeypatch complete_json / complete_text).
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
DEFAULT_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))


class LLMError(Exception):
    """Raised when every provider fails (missing keys, timeouts, bad output)."""


# --------------------------------------------------------------------------- #
# Lazy provider clients
# --------------------------------------------------------------------------- #
_groq_client = None
_gemini_configured = False


def _groq():
    global _groq_client
    if _groq_client is None:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            return None
        from groq import Groq

        _groq_client = Groq(api_key=key)
    return _groq_client


def _gemini():
    global _gemini_configured
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    import google.generativeai as genai

    if not _gemini_configured:
        genai.configure(api_key=key)
        _gemini_configured = True
    return genai


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("empty LLM response")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Salvage the first {...} block if the model wrapped it in prose/fences.
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError("no JSON object in response")
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("JSON response is not an object")
    return obj


# --------------------------------------------------------------------------- #
# Provider calls
# --------------------------------------------------------------------------- #
def _groq_chat(system: str, user: str, timeout: float, json_mode: bool) -> str:
    client = _groq()
    if client is None:
        raise LLMError("GROQ_API_KEY not set")
    kwargs = dict(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.1,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.with_options(timeout=timeout).chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _gemini_chat(system: str, user: str, timeout: float, json_mode: bool) -> str:
    genai = _gemini()
    if genai is None:
        raise LLMError("GEMINI_API_KEY not set")
    gen_cfg = {"temperature": 0.1}
    if json_mode:
        gen_cfg["response_mime_type"] = "application/json"
    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system)
    resp = model.generate_content(
        user, generation_config=gen_cfg, request_options={"timeout": timeout}
    )
    return resp.text or ""


# --------------------------------------------------------------------------- #
# Retry + fallback runner
# --------------------------------------------------------------------------- #
def _run(parse: Callable[[str], object], system: str, user: str,
         timeout: float, json_mode: bool):
    """Try Groq (2 attempts) then Gemini (2 attempts). Parse each raw output;
    a parse failure counts as a failed attempt so we retry / fall back."""
    last: Optional[Exception] = None
    for provider in (_groq_chat, _gemini_chat):
        for _attempt in range(2):
            try:
                raw = provider(system, user, timeout, json_mode)
                return parse(raw)
            except Exception as e:  # noqa: BLE001 - any failure -> retry/fallback
                last = e
    raise LLMError(f"all LLM providers failed: {last}")


def complete_json(system: str, user: str, schema_hint: Optional[str] = None,
                  timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Return a parsed JSON object from the LLM (Groq -> Gemini fallback)."""
    system = system + "\nRespond with a single valid JSON object and nothing else."
    if schema_hint:
        user = f"{user}\n\nReturn JSON with exactly this shape:\n{schema_hint}"
    return _run(_extract_json, system, user, timeout, json_mode=True)  # type: ignore[return-value]


def complete_text(system: str, user: str,
                  timeout: float = DEFAULT_TIMEOUT) -> str:
    """Return prose text from the LLM (Groq -> Gemini fallback)."""
    def parse(raw: str) -> str:
        if not raw or not raw.strip():
            raise ValueError("empty text response")
        return raw.strip()

    return _run(parse, system, user, timeout, json_mode=False)  # type: ignore[return-value]
