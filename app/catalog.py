"""Catalog loader + URL validation.

STUB — to be implemented. See CLAUDE.md engineering rules.

Responsibilities:
- Load data/shl_product_catalog.json ONCE at import/startup (377 records).
  NEVER fetch the catalog URL or shl.com at runtime.
- The URL field on each record is `link`; categories are in `keys` (list of full
  category names). Map `keys` -> letters via schemas.KEY_TO_LETTER for `test_type`.
- Provide fast lookup by url/link and by name.
- Provide validate_recommendations(items): drop any item whose url is not present
  verbatim as a `link` in the catalog. Never invent an assessment.
"""
from __future__ import annotations

# TODO(agent-task): implement catalog loading, indexing by link/name, and
# recommendation validation against the committed catalog.
