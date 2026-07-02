"""Catalog loader + URL/name validation — the hard-eval firewall.

Loads data/shl_product_catalog.json ONCE at import time (offline; the URL is never
fetched at runtime — see CLAUDE.md engineering rules) and exposes:

- get_by_url(url)            -> record | None   (exact, trailing-slash tolerant)
- find_by_name(name, thr)    -> record | None   (fuzzy: acronyms, partials, typos)
- validate_recommendations() -> cleaned list    (url/test_type ALWAYS from catalog)

Every /chat response must pass through validate_recommendations() so that no
invented assessment and no LLM-authored url/test_type can ever reach the wire.

Catalog schema (verified at setup): JSON list of 377 records. Each record's URL is
in `link` (NOT `url`); categories are in `keys` (full phrases). See CLAUDE.md.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

from app.schemas import KEY_TO_LETTER

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "shl_product_catalog.json"

# Canonical letter ordering for comma-joined test_type (matches CLAUDE.md map order).
_LETTER_ORDER = "ABCDEKPS"

# One catalog record's `name` was corrupted by unescaped control characters in the
# upstream JSON: the word "Excel" was destroyed, leaving "Microsoft \n    365 (New)".
# The url is intact and the correct name is knowable from the slug + ground truth, so
# we repair this single record by url at load time. (Verified: exactly 1 record is
# affected — see the setup scan in the decisions log.)
_NAME_REPAIRS: dict[str, str] = {
    "https://www.shl.com/products/product-catalog/view/microsoft-excel-365-new/":
        "Microsoft Excel 365 (New)",
}

# Known short-forms that are genuinely ambiguous by string similarity alone. The
# fuzzy matcher would otherwise tie "OPQ" across several OPQ* reports; the acronym
# canonically means the questionnaire itself. Kept tiny and explicit on purpose.
_ALIASES: dict[str, str] = {
    "opq": "Occupational Personality Questionnaire OPQ32r",
    "opq32r": "Occupational Personality Questionnaire OPQ32r",
    "verify g+": "SHL Verify Interactive G+",
    "verify interactive g+": "SHL Verify Interactive G+",
    "verify g plus": "SHL Verify Interactive G+",
}


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #
def _norm_text(s: str) -> str:
    """Lowercase; keep alphanumerics and '+'; collapse everything else to spaces."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9+]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _parse_duration_minutes(raw: str) -> Optional[int]:
    """'13 minutes' -> 13; '', '-', 'N/A', 'TBC', 'Untimed', 'Variable' -> None."""
    m = re.match(r"\s*(\d+)\s*minute", raw or "", flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _keys_to_test_type(keys: Iterable[str]) -> str:
    """Map full category phrases to comma-joined letters in canonical order."""
    letters = {KEY_TO_LETTER[k] for k in keys if k in KEY_TO_LETTER}
    return ",".join(sorted(letters, key=_LETTER_ORDER.index))


def _to_bool(v: Any) -> bool:
    return str(v).strip().lower() in {"yes", "true", "1"}


def _normalize_record(rec: dict) -> dict:
    url = rec.get("link", "") or ""
    # Collapse control chars / runs of whitespace (upstream data hygiene), then
    # apply targeted repairs for records with corruption-damaged names.
    name = re.sub(r"\s+", " ", (rec.get("name", "") or "")).strip()
    name = _NAME_REPAIRS.get(url, name)
    keys = rec.get("keys") or []
    job_levels = rec.get("job_levels") or []
    languages = rec.get("languages") or []
    description = rec.get("description", "") or ""
    duration_minutes = _parse_duration_minutes(rec.get("duration", ""))

    dur_phrase = f"{duration_minutes} minutes" if duration_minutes is not None else "unspecified"
    search_doc = (
        f"{name}. {description} "
        f"Types: {', '.join(keys)}. "
        f"Job levels: {', '.join(job_levels)}. "
        f"Duration: {dur_phrase}."
    )

    return {
        "entity_id": rec.get("entity_id"),
        "name": name,
        "url": url,
        "description": description,
        "keys": list(keys),
        "test_type": _keys_to_test_type(keys),
        "job_levels": list(job_levels),
        "languages": list(languages),
        "duration_minutes": duration_minutes,
        "remote": _to_bool(rec.get("remote")),
        "adaptive": _to_bool(rec.get("adaptive")),
        "search_doc": search_doc,
    }


# --------------------------------------------------------------------------- #
# Load at import
# --------------------------------------------------------------------------- #
def _load() -> list[dict]:
    with _CATALOG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    records = raw if isinstance(raw, list) else next(
        (v for v in raw.values() if isinstance(v, list)), []
    )
    return [_normalize_record(r) for r in records if isinstance(r, dict)]


CATALOG: list[dict] = _load()

# Indexes for fast lookup.
_BY_URL: dict[str, dict] = {_norm_url(r["url"]): r for r in CATALOG if r["url"]}
_BY_NORM_NAME: dict[str, dict] = {}
for _r in CATALOG:
    _BY_NORM_NAME.setdefault(_norm_text(_r["name"]), _r)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def all_records() -> list[dict]:
    return CATALOG


def get_by_url(url: str) -> Optional[dict]:
    """Exact url match, tolerant of trailing-slash differences."""
    return _BY_URL.get(_norm_url(url))


def find_by_name(name: str, threshold: float = 0.72) -> Optional[dict]:
    """Resolve a (possibly partial/typo'd/acronym) name to a catalog record.

    Layered, deterministic:
      1. curated alias for assignment-canonical shorthands (e.g. "OPQ",
         "Verify G+"). Checked FIRST because some shorthands collide with a
         distinct legacy product after normalization ("Verify G+" vs the separate
         "Verify - G+"); the traces and behavioral rule 3 always mean the
         Interactive G+, so the alias must win.
      2. exact normalized-name match
      3. best fuzzy score over all records; must clear `threshold`.
    """
    if not name or not name.strip():
        return None

    q = _norm_text(name)

    # 1. alias (assignment-canonical shorthands win over literal collisions)
    if q in _ALIASES:
        rec = _BY_NORM_NAME.get(_norm_text(_ALIASES[q]))
        if rec:
            return rec

    # 2. exact
    if q in _BY_NORM_NAME:
        return _BY_NORM_NAME[q]

    # 3. fuzzy
    best, best_score = None, 0.0
    q_tokens = q.split()
    for rec in CATALOG:
        score = _name_score(q, q_tokens, rec)
        if score > best_score:
            best, best_score = rec, score
    return best if best_score >= threshold else None


def _token_matches(qt: str, c_tokens: list[str]) -> bool:
    """Does query token `qt` correspond to some candidate token?

    Guards against short-token false friends: "rust" must NOT match "r".
    """
    for ct in c_tokens:
        if qt == ct:
            return True
        if len(qt) >= 3 and qt in ct:          # "opq" in "opq32r", "jav" in "java"
            return True
        if len(ct) >= 3 and ct in qt:
            return True
        if len(qt) >= 4 and SequenceMatcher(None, qt, ct).ratio() >= 0.85:  # typos
            return True
    return False


def _name_score(q: str, q_tokens: list[str], rec: dict) -> float:
    """Similarity in [0,1] between query and a record's name.

    Combines whole-string ratio with token coverage so that an unmatched
    distinctive token (e.g. "rust" against "r programming new") drags the score
    below threshold instead of riding a high char-overlap ratio to a false match.
    """
    c = _norm_text(rec["name"])
    if not c:
        return 0.0
    if q == c:
        return 1.0

    ratio = SequenceMatcher(None, q, c).ratio()
    if q in c:                                  # query fully contained
        return max(ratio, 0.9)

    c_tokens = c.split()
    if not q_tokens:
        return ratio
    matched = sum(1 for qt in q_tokens if _token_matches(qt, c_tokens))
    coverage = matched / len(q_tokens)
    if coverage == 1.0:                         # every query token accounted for
        return max(ratio, 0.75)
    return ratio * coverage                     # penalize unmatched tokens


def validate_recommendations(items: Iterable[Any], limit: int = 10) -> list[dict]:
    """Firewall: resolve each proposed item to a canonical catalog record.

    - name is resolved via find_by_name (falls back to a valid url if the name
      cannot be resolved);
    - url and test_type are ALWAYS taken from the catalog record, never the input;
    - unresolvable items are dropped; duplicates removed; order preserved; <= limit.
    """
    cleaned: list[dict] = []
    seen: set[str] = set()

    for item in items or []:
        name = _field(item, "name")
        url = _field(item, "url")

        rec = find_by_name(name) if name else None
        if rec is None and url:
            rec = get_by_url(url)
        if rec is None:
            continue

        key = _norm_url(rec["url"])
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {"name": rec["name"], "url": rec["url"], "test_type": rec["test_type"]}
        )
        if len(cleaned) >= limit:
            break

    return cleaned


def _field(item: Any, key: str) -> str:
    if isinstance(item, dict):
        return str(item.get(key, "") or "")
    return str(getattr(item, key, "") or "")
