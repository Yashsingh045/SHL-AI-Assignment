"""Replay harness: Recall@10 + contract compliance.

STUB — to be implemented. See CLAUDE.md.

Responsibilities:
- Load data/ground_truth.json. For each trace, replay user_turns against POST /chat
  (feeding full history each call, stateless), collect the FINAL response.
- Recall@10: fraction of ground-truth final_shortlist urls present in the final
  recommendations (compared by exact url).
- Contract checks: schema validity, catalog-only urls, <=10 items, <=8 turns,
  end_of_conversation set on close, non-empty recs when a shortlist existed.
- Write a summary to evals/results/.
"""
from __future__ import annotations

# TODO(agent-task): implement replay + Recall@10 + contract compliance scoring.
