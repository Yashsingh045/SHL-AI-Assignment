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
import time
from typing import Callable, Optional

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Per-call TOTAL budget (all attempts + backoff). Kept well under the 30s/turn eval
# budget so the agent's two sequential LLM calls both fit: ~2 x 13s < 30s.
DEFAULT_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "13"))
GROQ_RETRY_BACKOFF_S = float(os.getenv("GROQ_RETRY_BACKOFF_S", "2"))
# Optional client-side throttle: minimum seconds between Groq calls, to proactively
# stay under free-tier per-minute (TPM/RPM) limits. Default 0 (off) in prod/tests;
# the replay harness sets it (e.g. 6s) so bursty evals don't trip 429s.
GROQ_MIN_INTERVAL_S = float(os.getenv("GROQ_MIN_INTERVAL_S", "0"))
_last_groq_ts = 0.0


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
def _throttle_groq() -> None:
    """Sleep just enough to keep GROQ_MIN_INTERVAL_S between consecutive Groq calls."""
    global _last_groq_ts
    interval = GROQ_MIN_INTERVAL_S
    if interval > 0:
        wait = interval - (time.monotonic() - _last_groq_ts)
        if wait > 0:
            time.sleep(wait)
    _last_groq_ts = time.monotonic()


def _groq_chat(system: str, user: str, timeout: float, json_mode: bool,
               temperature: float) -> str:
    client = _groq()
    if client is None:
        raise LLMError("GROQ_API_KEY not set")
    _throttle_groq()
    kwargs = dict(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.with_options(timeout=timeout).chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _gemini_chat(system: str, user: str, timeout: float, json_mode: bool,
                 temperature: float) -> str:
    genai = _gemini()
    if genai is None:
        raise LLMError("GEMINI_API_KEY not set")
    gen_cfg = {"temperature": temperature}
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
         timeout: float, json_mode: bool, temperature: float):
    """Groq with one retry (~2s backoff between attempts) BEFORE falling back to
    Gemini (2 attempts). A parse failure counts as a failed attempt so we retry /
    fall back on garbled output too.

    `timeout` is the TOTAL budget for the whole call: each attempt is capped by the
    time remaining, and once the budget is spent we stop and raise. This keeps a
    single complete_*() call bounded so the agent's two calls fit the 30s turn budget
    even when a provider is slow (which previously stacked to ~60s)."""
    last: Optional[Exception] = None
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        return deadline - time.monotonic()

    # Primary: Groq, with a single backed-off retry on any error.
    for attempt in range(2):
        rem = remaining()
        if rem <= 1.0:
            break
        try:
            return parse(_groq_chat(system, user, rem, json_mode, temperature))
        except Exception as e:  # noqa: BLE001 - any failure -> retry then fallback
            last = e
            if (attempt == 0 and GROQ_RETRY_BACKOFF_S > 0
                    and remaining() > GROQ_RETRY_BACKOFF_S + 1.0):
                time.sleep(GROQ_RETRY_BACKOFF_S)

    # Fallback: Gemini.
    for _attempt in range(2):
        rem = remaining()
        if rem <= 1.0:
            break
        try:
            return parse(_gemini_chat(system, user, rem, json_mode, temperature))
        except Exception as e:  # noqa: BLE001
            last = e

    raise LLMError(f"all LLM providers failed: {last}")


def complete_json(system: str, user: str, schema_hint: Optional[str] = None,
                  timeout: float = DEFAULT_TIMEOUT, temperature: float = 0.1) -> dict:
    """Return a parsed JSON object from the LLM (Groq -> Gemini fallback)."""
    system = system + "\nRespond with a single valid JSON object and nothing else."
    if schema_hint:
        user = f"{user}\n\nReturn JSON with exactly this shape:\n{schema_hint}"
    return _run(_extract_json, system, user, timeout, True, temperature)  # type: ignore[return-value]


def complete_text(system: str, user: str,
                  timeout: float = DEFAULT_TIMEOUT, temperature: float = 0.1) -> str:
    """Return prose text from the LLM (Groq -> Gemini fallback)."""
    def parse(raw: str) -> str:
        if not raw or not raw.strip():
            raise ValueError("empty text response")
        return raw.strip()

    return _run(parse, system, user, timeout, False, temperature)  # type: ignore[return-value]
