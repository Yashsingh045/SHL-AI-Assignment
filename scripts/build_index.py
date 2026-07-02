"""Build the retrieval index from the committed catalog.

STUB — to be implemented. See CLAUDE.md.

Reads data/shl_product_catalog.json (committed, offline) and builds:
- a BM25 index over name + description,
- sentence-transformers embeddings for semantic retrieval,
persisted under data/index/ for app.retrieval to load at startup.
Never fetches the catalog URL or shl.com.

Run:  python scripts/build_index.py
"""
from __future__ import annotations

# TODO(agent-task): build and persist BM25 + embedding indices from the catalog.

if __name__ == "__main__":
    raise SystemExit("build_index.py is a stub — not implemented yet.")
