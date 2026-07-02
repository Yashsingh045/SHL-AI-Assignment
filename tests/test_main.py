"""FastAPI endpoint tests (TestClient) with a mocked LLM.

Warmup is skipped (SHL_WARMUP=0) so tests never load torch; the recommend path
monkeypatches retrieval.multi_search. Focus: contract shape, the never-500 fallbacks,
the max-10 clamp, and the firewall dropping injected fake assessments.
"""
from __future__ import annotations

import os

os.environ.setdefault("SHL_WARMUP", "0")  # must precede app import

import pytest
from fastapi.testclient import TestClient

from app import agent, catalog
from app.main import _truncate_history, app
from app.schemas import ChatMessage

client = TestClient(app)

JAVA = "Core Java (Advanced Level) (New)"
OPQ = "Occupational Personality Questionnaire OPQ32r"
VERIFY = "SHL Verify Interactive G+"


def _route_json(**over):
    base = {
        "intent": "clarify", "facts": {}, "current_shortlist_names": [],
        "edits": {"add": [], "remove": []}, "compare_targets": [],
        "aspects": [], "vague": True, "user_confirmed_done": False,
    }
    base.update(over)
    return base


@pytest.fixture
def mock_llm(monkeypatch):
    state = {"json_queue": [], "text": "Could you tell me more about the role?"}

    def fake_json(system, user, schema_hint=None, timeout=None):
        assert state["json_queue"], "unexpected complete_json call"
        return state["json_queue"].pop(0)

    def fake_text(system, user, timeout=None):
        return state["text"]

    monkeypatch.setattr(agent.llm, "complete_json", fake_json)
    monkeypatch.setattr(agent.llm, "complete_text", fake_text)
    return state


def _assert_valid_body(body: dict):
    assert set(body) == {"reply", "recommendations", "end_of_conversation"}
    assert isinstance(body["reply"], str) and body["reply"]
    assert isinstance(body["end_of_conversation"], bool)
    recs = body["recommendations"]
    assert recs is None or (isinstance(recs, list) and 1 <= len(recs) <= 10)
    for r in recs or []:
        assert set(r) == {"name", "url", "test_type"}
        assert r["url"].startswith("https://www.shl.com/")


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
def test_chat_happy_path_shape(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25: [catalog.find_by_name(JAVA)])
    mock_llm["json_queue"] = [
        _route_json(intent="recommend", vague=False, aspects=["Java"]),
        {"items": [JAVA, VERIFY, OPQ], "reply": "A battery for a Java role."},
    ]
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "Java developer"}]})
    assert r.status_code == 200
    body = r.json()
    _assert_valid_body(body)
    assert any(x["name"] == JAVA for x in body["recommendations"])


def test_chat_clarify_null_recs(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=True)]
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "we need something"}]})
    assert r.status_code == 200
    body = r.json()
    _assert_valid_body(body)
    assert body["recommendations"] is None


# --------------------------------------------------------------------------- #
# never-500 fallback paths
# --------------------------------------------------------------------------- #
def test_chat_llm_failure_returns_valid_fallback(monkeypatch):
    def boom(*a, **k):
        raise agent.llm.LLMError("down")
    monkeypatch.setattr(agent.llm, "complete_json", boom)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    _assert_valid_body(r.json())
    assert r.json()["recommendations"] is None


def test_chat_malformed_body_missing_messages():
    r = client.post("/chat", json={"foo": "bar"})
    assert r.status_code == 200
    _assert_valid_body(r.json())


def test_chat_invalid_json_body():
    r = client.post("/chat", content="this is not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 200
    _assert_valid_body(r.json())


def test_chat_bad_role_value():
    # role not in {"user","assistant"} -> validation error -> 200 fallback.
    r = client.post("/chat", json={"messages": [{"role": "system", "content": "x"}]})
    assert r.status_code == 200
    _assert_valid_body(r.json())


def test_chat_extra_unknown_fields_tolerated(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=True)]
    r = client.post("/chat", json={
        "session_id": "abc", "messages": [
            {"role": "user", "content": "hello", "timestamp": 123, "id": "m1"}
        ],
    })
    assert r.status_code == 200
    _assert_valid_body(r.json())


# --------------------------------------------------------------------------- #
# edge inputs
# --------------------------------------------------------------------------- #
def test_chat_empty_messages():
    r = client.post("/chat", json={"messages": []})
    assert r.status_code == 200
    body = r.json()
    _assert_valid_body(body)
    assert body["recommendations"] is None


def test_chat_history_starts_with_assistant(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=True)]
    r = client.post("/chat", json={"messages": [
        {"role": "assistant", "content": "Hi, how can I help?"},
        {"role": "user", "content": "we are hiring analysts"},
    ]})
    assert r.status_code == 200
    _assert_valid_body(r.json())


def test_chat_last_message_from_assistant(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=True)]
    r = client.post("/chat", json={"messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "What role are you hiring for?"},
    ]})
    assert r.status_code == 200
    _assert_valid_body(r.json())


def test_chat_non_english_input(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=True)]
    mock_llm["text"] = "Could you tell me more about the role?"
    r = client.post("/chat", json={"messages": [
        {"role": "user", "content": "Necesitamos evaluaciones para analistas financieros graduados"},
    ]})
    assert r.status_code == 200
    _assert_valid_body(r.json())


# --------------------------------------------------------------------------- #
# clamp + firewall
# --------------------------------------------------------------------------- #
def test_chat_recommendations_clamped_to_10(mock_llm, monkeypatch):
    names = [r["name"] for r in catalog.all_records()[:15]]
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25: [catalog.find_by_name(n) for n in names])
    mock_llm["json_queue"] = [
        _route_json(intent="recommend", vague=False, aspects=["x"]),
        {"items": names, "reply": "many"},
    ]
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "everything"}]})
    body = r.json()
    assert body["recommendations"] is not None
    assert len(body["recommendations"]) == 10


def test_chat_fake_assessment_dropped(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25: [catalog.find_by_name(JAVA)])
    mock_llm["json_queue"] = [
        _route_json(intent="recommend", vague=False, aspects=["Java"]),
        {"items": [JAVA, "Rust Programming (New)", "Totally Made Up Test"], "reply": "x"},
    ]
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "Java dev"}]})
    body = r.json()
    names = [x["name"] for x in body["recommendations"]]
    assert JAVA in names
    assert not any("Rust" in n or "Made Up" in n for n in names)


# --------------------------------------------------------------------------- #
# history truncation (unit)
# --------------------------------------------------------------------------- #
def test_truncate_history_keeps_first_user_and_recent():
    msgs = [ChatMessage(role="user", content="FIRST-USER-ANCHOR " + "x" * 1000)]
    for i in range(60):
        role = "assistant" if i % 2 == 0 else "user"
        msgs.append(ChatMessage(role=role, content=f"turn {i} " + "y" * 1000))
    out = _truncate_history(msgs, max_tokens=2000)
    assert out[0].content.startswith("FIRST-USER-ANCHOR")   # first user kept
    assert out[-1] is msgs[-1]                               # newest kept
    assert len(out) < len(msgs)                              # middle trimmed
    assert sum(len(m.content) for m in out) / 4 <= 2000 + 2000  # bounded


def test_truncate_history_noop_when_small():
    msgs = [ChatMessage(role="user", content="short")]
    assert _truncate_history(msgs) is msgs
