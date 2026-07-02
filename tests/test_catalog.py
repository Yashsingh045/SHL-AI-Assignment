"""Tests for the catalog firewall (app/catalog.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import catalog as C

ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))
GT_ITEMS = [
    (t["trace_id"], it["name"], it["url"])
    for t in GROUND_TRUTH
    for it in t["final_shortlist"]
]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def test_catalog_loads():
    assert len(C.CATALOG) == 377
    rec = C.CATALOG[0]
    for field in ("entity_id", "name", "url", "test_type", "search_doc", "keys"):
        assert field in rec


def test_duration_parsing():
    assert C._parse_duration_minutes("13 minutes") == 13
    assert C._parse_duration_minutes("1 minute") == 1
    for junk in ("", "-", "N/A", "TBC", "Untimed", "Variable"):
        assert C._parse_duration_minutes(junk) is None


# --------------------------------------------------------------------------- #
# URL lookup
# --------------------------------------------------------------------------- #
def test_get_by_url_exact():
    url = "https://www.shl.com/products/product-catalog/view/occupational-personality-questionnaire-opq32r/"
    rec = C.get_by_url(url)
    assert rec is not None
    assert rec["name"] == "Occupational Personality Questionnaire OPQ32r"
    assert rec["test_type"] == "P"


def test_get_by_url_trailing_slash_tolerant():
    base = "https://www.shl.com/products/product-catalog/view/shl-verify-interactive-g"
    assert C.get_by_url(base) is not None
    assert C.get_by_url(base + "/") is C.get_by_url(base)


def test_get_by_url_unknown():
    assert C.get_by_url("https://example.com/not-real/") is None


# --------------------------------------------------------------------------- #
# Fuzzy name resolution
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "query, expected",
    [
        ("OPQ32r", "Occupational Personality Questionnaire OPQ32r"),
        ("opq", "Occupational Personality Questionnaire OPQ32r"),
        ("Verify G+", "SHL Verify Interactive G+"),
        ("SHL Verify Interactive G+", "SHL Verify Interactive G+"),
        ("Core Jav (Advanced Level)", "Core Java (Advanced Level) (New)"),  # typo
    ],
)
def test_find_by_name_resolves(query, expected):
    rec = C.find_by_name(query)
    assert rec is not None, f"{query!r} did not resolve"
    assert rec["name"] == expected


def test_find_by_name_rejects_fake():
    # A distinctive unmatched token ("rust") must not ride overlap to "R Programming".
    assert C.find_by_name("Rust Programming (New)") is None


def test_find_by_name_empty():
    assert C.find_by_name("") is None
    assert C.find_by_name("   ") is None


@pytest.mark.parametrize("trace_id, name, url", GT_ITEMS, ids=[f"{t}:{n}" for t, n, _ in GT_ITEMS])
def test_all_ground_truth_names_resolve(trace_id, name, url):
    """Every labeled shortlist item must resolve, to the record at its labeled url."""
    rec = C.find_by_name(name)
    assert rec is not None, f"{trace_id}: {name!r} unresolved"
    assert C._norm_url(rec["url"]) == C._norm_url(url), (
        f"{trace_id}: {name!r} resolved to wrong record"
    )


# --------------------------------------------------------------------------- #
# validate_recommendations — the hard-eval firewall
# --------------------------------------------------------------------------- #
def test_validate_drops_fake_and_uses_catalog_fields():
    items = [
        # LLM-supplied url/test_type are bogus and must be overwritten from catalog.
        {"name": "Core Java (Advanced Level) (New)", "url": "http://evil.com", "test_type": "ZZZ"},
        {"name": "Rust Programming (New)"},  # fake -> dropped
        {"name": "SHL Verify Interactive G+"},
        {"name": "OPQ"},  # alias -> OPQ32r
    ]
    out = C.validate_recommendations(items)

    names = [o["name"] for o in out]
    assert "Rust Programming (New)" not in " ".join(names)
    assert names == [
        "Core Java (Advanced Level) (New)",
        "SHL Verify Interactive G+",
        "Occupational Personality Questionnaire OPQ32r",
    ]
    # url/test_type always from catalog, never from the input dict.
    core = out[0]
    assert core["url"].startswith("https://www.shl.com/")
    assert core["test_type"] == "K"


def test_validate_preserves_order():
    names = ["SQL (New)", "Spring (New)", "Docker (New)"]
    out = C.validate_recommendations([{"name": n} for n in names])
    assert [o["name"] for o in out] == names


def test_validate_dedupes_and_clamps_to_10():
    dupes = [{"name": "OPQ"}, {"name": "OPQ32r"},
             {"name": "Occupational Personality Questionnaire OPQ32r"}]
    assert len(C.validate_recommendations(dupes)) == 1

    many = [{"name": r["name"]} for r in C.CATALOG[:15]]
    assert len(C.validate_recommendations(many)) == 10


def test_validate_handles_malformed_input():
    assert C.validate_recommendations([]) == []
    assert C.validate_recommendations(None) == []
    # Missing/blank names are dropped without raising.
    assert C.validate_recommendations([{"foo": "bar"}, {"name": ""}]) == []
