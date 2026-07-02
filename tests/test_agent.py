"""Agent branch tests with a mocked LLM, plus one real-LLM integration test.

The LLM and retrieval are monkeypatched so unit tests are deterministic and never hit
the network or load torch. The final integration test runs only when GROQ_API_KEY or
GEMINI_API_KEY is set.
"""
from __future__ import annotations

import json
import os

import pytest

from app import agent, catalog
from app.schemas import ChatResponse

# Canonical catalog names used across tests.
JAVA = "Core Java (Advanced Level) (New)"
SPRING = "Spring (New)"
SQL = "SQL (New)"
AWS = "Amazon Web Services (AWS) Development (New)"
DOCKER = "Docker (New)"
VERIFY = "SHL Verify Interactive G+"
OPQ = "Occupational Personality Questionnaire OPQ32r"


def _rec(name):
    return catalog.find_by_name(name)


def _shortlist_msg(names):
    """An assistant message whose content embeds a shortlist table (as the agent emits)."""
    rows = "\n".join(
        f"| {i} | {n} | X | {_rec(n)['url']} |" for i, n in enumerate(names, 1)
    )
    return {"role": "assistant",
            "content": "Here's a shortlist:\n\n| # | Name | Test Type | URL |\n|---|------|-----------|-----|\n" + rows}


@pytest.fixture
def mock_llm(monkeypatch):
    """Router returns queued dict(s); text calls return a fixed string."""
    state = {"json_queue": [], "text": "MOCK TEXT REPLY"}

    def fake_json(system, user, schema_hint=None, timeout=None):
        assert state["json_queue"], "unexpected complete_json call"
        return state["json_queue"].pop(0)

    def fake_text(system, user, timeout=None):
        return state["text"]

    monkeypatch.setattr(agent.llm, "complete_json", fake_json)
    monkeypatch.setattr(agent.llm, "complete_text", fake_text)
    return state


def _route_json(**over):
    base = {
        "intent": "clarify", "facts": {}, "current_shortlist_names": [],
        "edits": {"add": [], "remove": []}, "compare_targets": [],
        "aspects": [], "vague": True, "user_confirmed_done": False,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# clarify
# --------------------------------------------------------------------------- #
def test_clarify_no_recs(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=True)]
    mock_llm["text"] = "Who is this assessment for?"
    resp = agent.handle_chat([{"role": "user", "content": "We need a solution for senior leadership."}])
    assert isinstance(resp, ChatResponse)
    assert resp.recommendations is None
    assert resp.end_of_conversation is False
    assert "?" in resp.reply


def test_clarify_budget_forces_recommend(mock_llm, monkeypatch):
    # Two prior clarifying assistant turns already used -> a 3rd clarify is overridden.
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25, **kw: [_rec(JAVA)])
    mock_llm["json_queue"] = [
        _route_json(intent="clarify", vague=False, facts={"skills": ["Java"]}, aspects=["Java"]),
        {"items": [JAVA, OPQ], "reply": "Here is a battery."},  # recommend LLM step
    ]
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "What role?"},
        {"role": "user", "content": "an engineer"},
        {"role": "assistant", "content": "Which stack?"},
        {"role": "user", "content": "Java"},
    ]
    resp = agent.handle_chat(messages)
    assert resp.recommendations is not None  # was forced to recommend


# --------------------------------------------------------------------------- #
# recommend
# --------------------------------------------------------------------------- #
def test_recommend_selects_and_validates(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25, **kw: [_rec(JAVA), _rec(SPRING), _rec(SQL)])
    mock_llm["json_queue"] = [
        _route_json(intent="recommend", vague=False,
                    facts={"skills": ["Java", "Spring", "SQL"]},
                    aspects=["Java", "Spring", "SQL"]),
        {"items": [JAVA, SPRING, SQL, VERIFY, OPQ], "reply": "Battery for a backend engineer."},
    ]
    resp = agent.handle_chat([{"role": "user", "content": "backend engineer: Java, Spring, SQL"}])
    urls = [r.url for r in resp.recommendations]
    names = [r.name for r in resp.recommendations]
    assert JAVA in names and OPQ in names
    assert all(u.startswith("https://www.shl.com/") for u in urls)
    # url + test_type come from the catalog, and the reply embeds the table.
    assert resp.recommendations[0].test_type
    assert "| Name |" in resp.reply


def test_recommend_drops_hallucinated_item(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25, **kw: [_rec(JAVA)])
    mock_llm["json_queue"] = [
        _route_json(intent="recommend", vague=False, aspects=["Java"]),
        {"items": [JAVA, "Rust Programming (New)"], "reply": "x"},  # fake dropped
    ]
    resp = agent.handle_chat([{"role": "user", "content": "Java dev"}])
    names = [r.name for r in resp.recommendations]
    assert JAVA in names
    assert not any("Rust" in n for n in names)


def test_recommend_empty_falls_back_to_retrieval(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "multi_search",
                        lambda aspects, top_k=25, **kw: [_rec(JAVA), _rec(SPRING)])
    mock_llm["json_queue"] = [
        _route_json(intent="recommend", vague=False, aspects=["Java"]),
        {"items": ["Totally Fake Test"], "reply": "x"},  # all invalid -> fallback
    ]
    resp = agent.handle_chat([{"role": "user", "content": "Java dev"}])
    assert resp.recommendations is not None and len(resp.recommendations) >= 1


# --------------------------------------------------------------------------- #
# refine (deterministic — only the router LLM call)
# --------------------------------------------------------------------------- #
def test_refine_surgical_add_drop(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "search",
                        lambda q, top_k=1, **kw: {"aws": [_rec(AWS)], "docker": [_rec(DOCKER)]}.get(
                            q.lower().split()[0], []))
    mock_llm["json_queue"] = [
        _route_json(intent="refine", vague=False,
                    current_shortlist_names=[JAVA, SPRING, "RESTful Web Services (New)", SQL],
                    edits={"add": ["AWS", "Docker"], "remove": ["RESTful Web Services (New)"]}),
    ]
    history = [
        {"role": "user", "content": "backend engineer"},
        _shortlist_msg([JAVA, SPRING, "RESTful Web Services (New)", SQL]),
        {"role": "user", "content": "Add AWS and Docker, drop REST"},
    ]
    resp = agent.handle_chat(history)
    names = [r.name for r in resp.recommendations]
    assert "RESTful Web Services (New)" not in names   # dropped
    assert AWS in names and DOCKER in names            # added
    assert names[:3] == [JAVA, SPRING, SQL]            # order preserved, adds appended


def test_refine_impossible_add_pushback(mock_llm, monkeypatch):
    monkeypatch.setattr(agent.retrieval, "search", lambda q, top_k=1, **kw: [])
    mock_llm["json_queue"] = [
        _route_json(intent="refine", vague=False,
                    current_shortlist_names=[JAVA],
                    edits={"add": ["Rust"], "remove": []}),
    ]
    history = [_shortlist_msg([JAVA]), {"role": "user", "content": "add a Rust test"}]
    resp = agent.handle_chat(history)
    # Honest: shortlist unchanged, no invented item.
    names = [r.name for r in resp.recommendations]
    assert names == [JAVA]
    assert "couldn't find" in resp.reply.lower() or "no catalog" in resp.reply.lower()


# --------------------------------------------------------------------------- #
# compare / refuse / close
# --------------------------------------------------------------------------- #
def test_compare_keeps_shortlist(mock_llm):
    mock_llm["json_queue"] = [
        _route_json(intent="compare", vague=False,
                    compare_targets=[JAVA, "Core Java (Entry Level) (New)"])
    ]
    mock_llm["text"] = "Advanced covers deeper topics than Entry."
    history = [_shortlist_msg([JAVA, OPQ]), {"role": "user", "content": "difference between advanced and entry java?"}]
    resp = agent.handle_chat(history)
    assert resp.recommendations is not None  # existing shortlist stays attached
    assert resp.end_of_conversation is False


def test_refuse_partial_keeps_shortlist_and_continues(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="refuse_partial", vague=False)]
    mock_llm["text"] = "I can only help select SHL assessments."
    history = [_shortlist_msg([JAVA, OPQ]),
               {"role": "user", "content": "Does this battery satisfy HIPAA legally?"}]
    resp = agent.handle_chat(history)
    assert resp.recommendations is not None
    assert resp.end_of_conversation is False


def test_smalltalk_close_reemits_full_shortlist(mock_llm):
    mock_llm["json_queue"] = [_route_json(intent="smalltalk_close", vague=False,
                                          user_confirmed_done=True,
                                          current_shortlist_names=[JAVA, VERIFY, OPQ])]
    history = [_shortlist_msg([JAVA, VERIFY, OPQ]),
               {"role": "user", "content": "Perfect, that's what we need."}]
    resp = agent.handle_chat(history)
    assert resp.end_of_conversation is True
    names = [r.name for r in resp.recommendations]
    assert names == [JAVA, VERIFY, OPQ]  # full shortlist re-emitted, non-empty


# --------------------------------------------------------------------------- #
# defensive
# --------------------------------------------------------------------------- #
def test_router_failure_defaults_to_clarify(monkeypatch):
    def boom(*a, **k):
        raise agent.llm.LLMError("down")
    monkeypatch.setattr(agent.llm, "complete_json", boom)
    resp = agent.handle_chat([{"role": "user", "content": "hello"}])
    assert resp.recommendations is None
    assert resp.end_of_conversation is False
    assert resp.reply  # non-empty safe fallback


def test_empty_messages_safe():
    resp = agent.handle_chat([])
    assert isinstance(resp, ChatResponse)
    assert resp.recommendations is None


# --------------------------------------------------------------------------- #
# STAGE 2 — shortlist persistence at the response boundary (rule 5, in code)
# --------------------------------------------------------------------------- #
def test_clarify_after_shortlist_carries_shortlist(mock_llm):
    # A clarify turn AFTER a shortlist exists must re-attach the full shortlist.
    mock_llm["json_queue"] = [_route_json(intent="clarify", vague=False)]
    mock_llm["text"] = "Which language variant do you need?"
    history = [
        {"role": "user", "content": "java role"},
        _shortlist_msg([JAVA, OPQ]),
        {"role": "user", "content": "hmm, one more thing"},
    ]
    resp = agent.handle_chat(history)
    assert resp.recommendations is not None
    assert [r.name for r in resp.recommendations] == [JAVA, OPQ]
    assert resp.end_of_conversation is False


def test_llm_failure_after_shortlist_carries_shortlist(monkeypatch):
    # Router LLM fails AFTER a shortlist exists -> fallback must still carry it.
    def boom(*a, **k):
        raise agent.llm.LLMError("429")
    monkeypatch.setattr(agent.llm, "complete_json", boom)
    history = [
        {"role": "user", "content": "backend engineer"},
        _shortlist_msg([JAVA, VERIFY, OPQ]),
        {"role": "user", "content": "and something about testing?"},
    ]
    resp = agent.handle_chat(history)
    assert resp.recommendations is not None
    assert [r.name for r in resp.recommendations] == [JAVA, VERIFY, OPQ]
    assert resp.end_of_conversation is False
    assert "trouble" in resp.reply.lower() or "wrong" in resp.reply.lower()


# --------------------------------------------------------------------------- #
# real-LLM integration (skipped without a key)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not (os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY")),
    reason="no LLM API key set",
)
def test_router_extracts_multiple_skills_real_llm():
    from app import agent as real_agent
    jd = (
        'Here\'s the JD for an engineer. "Senior Full-Stack Engineer — 5+ years across '
        "Core Java, Spring, REST API design, Angular, SQL/relational databases, AWS "
        'deployment, and Docker."'
    )
    real_agent._start_time = __import__("time").monotonic()
    route = real_agent._route([{"role": "user", "content": jd}])
    skills = route.facts.get("skills") or []
    blob = json.dumps(route.facts).lower() + " " + " ".join(route.aspects).lower()
    assert len(skills) >= 3 or sum(t in blob for t in ["java", "spring", "sql", "aws", "docker"]) >= 3
