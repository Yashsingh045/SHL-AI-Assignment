"""Conversation policy / orchestration.

STUB — to be implemented. See CLAUDE.md behavioral rules (1-10).

Responsibilities (stateless — derive everything from the messages array):
- Decide per turn: clarify (ONE question, <=2 clarify turns) vs recommend vs
  refuse vs compare, per behavioral rules 1-2, 6, 9.
- Build the default battery (rule 3): one K test per named tech + Verify G+ (A)
  for professional/graduate roles + OPQ32r (P) as removable default.
- Surgical refinement (rule 4): edit the CURRENT shortlist parsed from history;
  retrieve only for added concepts, preserve order.
- Carry the current full shortlist on every non-clarify/non-refusal response and
  re-emit it with end_of_conversation=true on the closing turn (rule 5).
- Never invent (rules 7-8). Validate all urls via catalog before returning.
- Budget: <= 2 LLM calls per request; always return a schema-valid ChatResponse.
"""
from __future__ import annotations

# TODO(agent-task): implement the per-turn policy that produces a ChatResponse.
