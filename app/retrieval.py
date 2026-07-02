"""Retrieval over the catalog (BM25 + embeddings).

STUB — to be implemented. See CLAUDE.md.

Responsibilities:
- Hybrid retrieval: rank-bm25 over name/description + sentence-transformers
  embeddings for semantic recall of tech/skill/role queries.
- retrieve(concept, k) -> ranked catalog records for a single concept (e.g. one
  named technology). Used to fetch one Knowledge (K) test per named skill and to
  resolve added concepts during surgical refinement (behavioral rule 4).
- Index is built by scripts/build_index.py from the committed catalog only.
"""
from __future__ import annotations

# TODO(agent-task): implement BM25 + embedding hybrid retrieval and per-concept lookup.
