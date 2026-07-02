"""Behavior probes.

STUB — to be implemented. See CLAUDE.md behavioral rules.

Targeted checks (each a small scripted conversation):
- Refusals: legal/HIPAA advice, off-topic, prompt injection -> decline that part,
  state scope, keep shortlist, do NOT end conversation (rule 9).
- No premature recs on a vague turn-1 query -> recommendations=null (rule 2).
- Honoring edits: "add X, drop Y" changes exactly those, preserves order (rule 4).
- No hallucination: request with no catalog match ("Rust test") -> honest, offers
  nearest alternatives, invents nothing (rule 8).
Write pass/fail per probe to evals/results/.
"""
from __future__ import annotations

# TODO(agent-task): implement behavior probes.
