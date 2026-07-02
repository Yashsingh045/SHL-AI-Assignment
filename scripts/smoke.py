"""Smoke test a deployed instance: python scripts/smoke.py https://<app>.onrender.com

Asserts:
  1. GET /health -> {"status": "ok"} with HTTP 200.
  2. A full multi-turn /chat conversation (the C9 senior full-stack JD flow) where
     EVERY response is schema-valid, catalog-only, and the FINAL response has a
     non-empty shortlist with end_of_conversation=true.

Exits non-zero on the first failed assertion (prints the offending payload).
"""
from __future__ import annotations

import sys

import httpx

# The C9 flow: JD naming multiple techs, a surgical refine, then confirmation/close.
USER_TURNS = [
    'Here is the JD for an engineer we need to fill. Can you recommend an assessment '
    'battery? "Senior Full-Stack Engineer — 5+ years across Core Java, Spring, REST API '
    'design, Angular, SQL/relational databases, AWS deployment, and Docker. Senior IC."',
    "Backend-leaning: Core Java, Spring, and SQL are primary; Angular is occasional.",
    "Add AWS and Docker. Drop REST.",
    "Keep Verify G+. That's perfect, thanks — locking it in.",
]

SHL_PREFIX = "https://www.shl.com/"


def _fail(msg: str, payload=None) -> None:
    print(f"FAIL: {msg}")
    if payload is not None:
        print(f"      payload: {payload}")
    sys.exit(1)


def _assert_schema(body: dict, turn: int) -> None:
    if set(body) != {"reply", "recommendations", "end_of_conversation"}:
        _fail(f"turn {turn}: response keys != contract", body)
    if not isinstance(body["reply"], str) or not body["reply"]:
        _fail(f"turn {turn}: reply not a non-empty string", body)
    if not isinstance(body["end_of_conversation"], bool):
        _fail(f"turn {turn}: end_of_conversation not a bool", body)
    recs = body["recommendations"]
    if recs is not None:
        if not isinstance(recs, list) or not (1 <= len(recs) <= 10):
            _fail(f"turn {turn}: recommendations must be null or 1-10 items", body)
        for r in recs:
            if set(r) != {"name", "url", "test_type"}:
                _fail(f"turn {turn}: recommendation item keys != contract", r)
            if not str(r["url"]).startswith(SHL_PREFIX):
                _fail(f"turn {turn}: non-catalog url", r)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/smoke.py <base_url>")
        sys.exit(2)
    base = sys.argv[1].rstrip("/")

    # 1. health
    h = httpx.get(f"{base}/health", timeout=30)
    if h.status_code != 200 or h.json() != {"status": "ok"}:
        _fail(f"/health -> {h.status_code} {h.text!r}")
    print("PASS: GET /health -> 200 {'status': 'ok'}")

    # 2. multi-turn /chat
    messages: list[dict] = []
    final = None
    for i, turn in enumerate(USER_TURNS, 1):
        messages.append({"role": "user", "content": turn})
        r = httpx.post(f"{base}/chat", json={"messages": messages}, timeout=35)
        if r.status_code != 200:
            _fail(f"turn {i}: POST /chat -> {r.status_code}", r.text)
        body = r.json()
        _assert_schema(body, i)
        n = len(body["recommendations"] or [])
        print(f"PASS: turn {i}: schema-valid, {n} recs, eoc={body['end_of_conversation']}")
        messages.append({"role": "assistant", "content": body["reply"]})
        final = body

    # final must carry a non-empty shortlist and close the conversation
    if not final or not final["recommendations"]:
        _fail("final response has empty recommendations", final)
    if not final["end_of_conversation"]:
        _fail("final response end_of_conversation is not true", final)

    print(f"\nSMOKE OK: final shortlist has {len(final['recommendations'])} items, "
          f"end_of_conversation=true")


if __name__ == "__main__":
    main()
