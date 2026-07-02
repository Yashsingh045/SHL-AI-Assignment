"""Retrieval-only baseline: Recall@10 / @20 of a naive query vs labeled shortlists.

For each trace we form ONE naive query by joining all user_turns and feed it to
multi_search as a single aspect. This deliberately skips all agent logic (no
aspect decomposition, no battery rules) so the numbers are a floor / yardstick to
beat once the agent lands. Expect mediocre recall.

Run:  python evals/retrieval_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app import catalog
from app.retrieval import multi_search

GROUND_TRUTH = _ROOT / "data" / "ground_truth.json"


def _recall_at_k(retrieved_urls: list[str], gold_urls: set[str], k: int) -> float:
    if not gold_urls:
        return 0.0
    top = {catalog._norm_url(u) for u in retrieved_urls[:k]}
    hits = len(top & gold_urls)
    return hits / len(gold_urls)


def main() -> None:
    traces = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))

    rows = []
    for t in traces:
        query = " ".join(t["user_turns"])
        gold = {catalog._norm_url(it["url"]) for it in t["final_shortlist"]}
        retrieved = [r["url"] for r in multi_search([query], top_k=20)]
        r10 = _recall_at_k(retrieved, gold, 10)
        r20 = _recall_at_k(retrieved, gold, 20)
        rows.append((t["trace_id"], len(gold), r10, r20))

    print(f"{'trace':6} {'gold':>4} {'R@10':>7} {'R@20':>7}")
    print("-" * 28)
    for tid, n, r10, r20 in rows:
        print(f"{tid:6} {n:>4} {r10:>7.2f} {r20:>7.2f}")
    mean10 = sum(r for _, _, r, _ in rows) / len(rows)
    mean20 = sum(r for _, _, _, r in rows) / len(rows)
    print("-" * 28)
    print(f"{'MEAN':6} {'':>4} {mean10:>7.2f} {mean20:>7.2f}")


if __name__ == "__main__":
    main()
