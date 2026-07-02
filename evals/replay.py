"""Replay harness — mirrors SHL's official LLM-simulated-user evaluation.

For each trace we build a persona from its user_turns, drive a multi-turn dialogue
(simulated user <-> our agent), then score the FINAL agent response:
  Recall@10 = |final recs ∩ ground-truth urls| / |ground-truth urls|   (normalized url)

Also asserts the hard-eval invariants on EVERY agent turn: schema-valid body and
catalog-only urls (these should be impossible to violate — we assert, not hope).

Usage:
  python evals/replay.py                 # drive handle_chat() directly (fast)
  python evals/replay.py --http URL      # drive a running instance's POST /chat
  python evals/replay.py --only C1,C9    # subset
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app import catalog, llm
from app.agent import handle_chat

GROUND_TRUTH = _ROOT / "data" / "ground_truth.json"
RESULTS_DIR = _ROOT / "evals" / "results"

MAX_MESSAGES = 8          # user + assistant combined (rule 10)
CALL_TIMEOUT_S = 30.0     # per call (rule 10)
_PACE_S = 0.0             # optional sleep between agent turns (free-tier rate-limit relief)

# (no shared pool: each timed call gets its own executor so a call that overruns
# the 30s budget can't block subsequent calls — see _call_with_timeout.)


# --------------------------------------------------------------------------- #
# Simulated user (Groq via llm.complete_text)
# --------------------------------------------------------------------------- #
def _persona_system(user_turns: list[str]) -> str:
    facts = " ".join(user_turns)
    return (
        "You are a hiring stakeholder. Your facts (things you know and can answer "
        f"truthfully): {facts}. Rules: answer the agent's questions truthfully ONLY "
        "from your facts; if asked something not covered by your facts, say you have "
        "no preference; open the conversation with your first fact/request; when the "
        "agent gives you a suitable shortlist, confirm and wrap up "
        "('That works, thanks.')."
    )


def _user_perspective_transcript(messages: list[dict]) -> str:
    from app.agent import strip_table_urls
    return "\n".join(
        f"{'ASSISTANT' if m['role'] == 'assistant' else 'YOU'}: {strip_table_urls(m['content'])}"
        for m in messages
    )


def _simulate_user(persona_system: str, messages: list[dict]) -> str:
    prompt = (
        "Conversation so far:\n" + _user_perspective_transcript(messages) +
        "\n\nReply with your next short message as the hiring stakeholder. If the "
        "assistant's latest shortlist already meets your needs, confirm and wrap up "
        "with 'That works, thanks.'"
    )
    try:
        # temperature=0 -> deterministic simulated user (stable run-to-run scoring).
        return _call_with_timeout(
            lambda: llm.complete_text(persona_system, prompt, temperature=0.0)
        )
    except Exception:
        # If the simulated user fails, wrap up so the dialogue terminates cleanly.
        return "That works, thanks."


# --------------------------------------------------------------------------- #
# Agent invocation (direct or HTTP), normalized to a dict + validated
# --------------------------------------------------------------------------- #
def _call_with_timeout(fn):
    # Fresh single-use executor per call: on timeout we abandon the still-running
    # thread (can't be killed) without blocking later calls; it drains in the bg.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=CALL_TIMEOUT_S)
    finally:
        ex.shutdown(wait=False)


def _agent_response(messages: list[dict], http_url: str | None) -> dict:
    if http_url:
        import httpx

        def _post():
            r = httpx.post(f"{http_url.rstrip('/')}/chat",
                           json={"messages": messages}, timeout=CALL_TIMEOUT_S)
            r.raise_for_status()
            return r.json()

        return _call_with_timeout(_post)

    resp = _call_with_timeout(lambda: handle_chat(messages))
    return resp.model_dump()


def _validate_agent_body(body: dict) -> tuple[bool, bool]:
    """Return (invalid_schema, non_catalog_url). Both should always be False."""
    invalid_schema = False
    non_catalog_url = False

    if set(body) != {"reply", "recommendations", "end_of_conversation"}:
        invalid_schema = True
    if not isinstance(body.get("reply"), str) or not isinstance(
        body.get("end_of_conversation"), bool
    ):
        invalid_schema = True

    recs = body.get("recommendations")
    if recs is not None:
        if not isinstance(recs, list) or not (1 <= len(recs) <= 10):
            invalid_schema = True
        else:
            for r in recs:
                if not isinstance(r, dict) or set(r) != {"name", "url", "test_type"}:
                    invalid_schema = True
                    continue
                if catalog.get_by_url(r["url"]) is None:
                    non_catalog_url = True
    return invalid_schema, non_catalog_url


# --------------------------------------------------------------------------- #
# One trace
# --------------------------------------------------------------------------- #
def replay_trace(trace: dict, http_url: str | None) -> dict:
    persona = _persona_system(trace["user_turns"])
    messages: list[dict] = [{"role": "user", "content": trace["user_turns"][0]}]

    transcript: list[dict] = list(messages)
    final_body: dict | None = None
    any_invalid_schema = False
    any_non_catalog_url = False

    timed_out = False
    while True:
        try:
            body = _agent_response(messages, http_url)
        except Exception:
            # Turn exceeded the 30s budget (or errored). Record and stop; scoring
            # uses the last good response (final_body) which may still hold a shortlist.
            timed_out = True
            break
        bad_schema, bad_url = _validate_agent_body(body)
        any_invalid_schema = any_invalid_schema or bad_schema
        any_non_catalog_url = any_non_catalog_url or bad_url

        assistant_msg = {"role": "assistant", "content": body.get("reply", "")}
        messages.append(assistant_msg)
        transcript.append(assistant_msg)
        final_body = body

        if body.get("end_of_conversation"):
            break
        if len(messages) >= MAX_MESSAGES:
            break

        if _PACE_S:
            time.sleep(_PACE_S)
        user_text = _simulate_user(persona, messages)
        user_msg = {"role": "user", "content": user_text}
        messages.append(user_msg)
        transcript.append(user_msg)

    # ---- score the final agent response ----
    final_recs = final_body.get("recommendations") or [] if final_body else []
    final_urls = {catalog._norm_url(r["url"]) for r in final_recs[:10]}
    gt_urls = {catalog._norm_url(it["url"]) for it in trace["final_shortlist"]}
    hits = len(final_urls & gt_urls)
    recall = hits / len(gt_urls) if gt_urls else 0.0
    matched_gt = [it["name"] for it in trace["final_shortlist"]
                  if catalog._norm_url(it["url"]) in final_urls]
    missed_gt = [it["name"] for it in trace["final_shortlist"]
                 if catalog._norm_url(it["url"]) not in final_urls]

    return {
        "trace_id": trace["trace_id"],
        "recall_at_10": round(recall, 3),
        "hits": hits,
        "gold_count": len(gt_urls),
        "turns_used": len(messages),
        "ended_true": bool(final_body and final_body.get("end_of_conversation")),
        "timed_out": timed_out,
        "invalid_schema": any_invalid_schema,
        "non_catalog_url": any_non_catalog_url,
        "final_recommendations": final_recs,
        "matched_gt": matched_gt,
        "missed_gt": missed_gt,
        "ground_truth_urls": sorted(gt_urls),
        "transcript": transcript,
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _note(row: dict) -> str:
    notes = []
    if row["invalid_schema"]:
        notes.append("INVALID SCHEMA")
    if row["non_catalog_url"]:
        notes.append("NON-CATALOG URL")
    if row.get("timed_out"):
        notes.append("TIMED OUT")
    if not row["final_recommendations"]:
        notes.append("empty final recs")
    if not row["ended_true"]:
        notes.append("no eoc")
    if row["turns_used"] >= MAX_MESSAGES and not row["ended_true"]:
        notes.append("hit turn cap")
    return ", ".join(notes) or "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", default=None, help="Base URL of a running instance")
    ap.add_argument("--only", default=None, help="Comma-separated trace ids, e.g. C1,C9")
    ap.add_argument("--out", default=None, help="Output json path")
    ap.add_argument("--runs", type=int, default=1, help="Repeat the full replay N times")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="Seconds to sleep between agent turns (free-tier rate-limit relief)")
    args = ap.parse_args()

    global _PACE_S
    _PACE_S = args.pace

    traces = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        traces = [t for t in traces if t["trace_id"] in wanted]

    if not args.http:  # warm index + embedding model so the first call isn't slow
        try:
            from app import retrieval
            retrieval.warmup()
        except Exception:
            pass

    runs: list[list[dict]] = []
    for run_i in range(args.runs):
        rows = []
        for t in traces:
            print(f"[run {run_i + 1}/{args.runs}] replaying {t['trace_id']} ...", flush=True)
            rows.append(replay_trace(t, args.http))
        runs.append(rows)

    # Aggregate per-trace across runs.
    order = [t["trace_id"] for t in traces]
    per_trace: dict[str, dict] = {}
    for tid in order:
        rws = [r for rows in runs for r in rows if r["trace_id"] == tid]
        recalls = [r["recall_at_10"] for r in rws]
        mean_r = sum(recalls) / len(recalls) if recalls else 0.0
        # Count how often each gold item was missed across runs.
        missed_counts: dict[str, int] = {}
        for r in rws:
            for name in r["missed_gt"]:
                missed_counts[name] = missed_counts.get(name, 0) + 1
        per_trace[tid] = {
            "mean_recall_at_10": round(mean_r, 3),
            "recalls": recalls,
            "gold_count": rws[0]["gold_count"] if rws else 0,
            "turns_last": rws[-1]["turns_used"] if rws else 0,
            "notes_last": _note(rws[-1]) if rws else "",
            "missed_counts": dict(sorted(missed_counts.items(), key=lambda kv: -kv[1])),
        }

    overall_mean = (
        sum(pt["mean_recall_at_10"] for pt in per_trace.values()) / len(per_trace)
        if per_trace else 0.0
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"replay_{ts}.json"
    out_path.write_text(
        json.dumps(
            {"timestamp": ts, "mode": "http" if args.http else "direct",
             "runs": args.runs, "overall_mean_recall_at_10": round(overall_mean, 3),
             "per_trace": per_trace, "raw_runs": runs},
            indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print(f"{'trace':6} {'meanR@10':>9} {'runs (per-run)':>18} {'turns':>6}  notes")
    print("-" * 78)
    for tid in order:
        pt = per_trace[tid]
        per_run = "[" + ",".join(f"{x:.2f}" for x in pt["recalls"]) + "]"
        print(f"{tid:6} {pt['mean_recall_at_10']:>9.2f} {per_run:>18} "
              f"{pt['turns_last']:>6}  {pt['notes_last']}")
    print("-" * 78)
    print(f"{'MEAN':6} {overall_mean:>9.2f}   (runs={args.runs})")
    print(f"\nWrote {out_path}")

    # Hard-eval invariants must never be violated.
    all_rows = [r for rows in runs for r in rows]
    assert not any(r["invalid_schema"] for r in all_rows), "invalid schema emitted"
    assert not any(r["non_catalog_url"] for r in all_rows), "non-catalog url emitted"


if __name__ == "__main__":
    main()
