"""Download the official SHL product catalog ONCE and commit it to the repo.

Run manually during setup:  python scripts/download_catalog.py

NEVER call this at runtime. The deployed service loads the committed file
data/shl_product_catalog.json only. This script exists purely to document and
reproduce how the committed catalog was obtained.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

CATALOG_URL = (
    "https://tcp-us-prod-rnd.shl.com/voiceRater/shl-ai-hiring/shl_product_catalog.json"
)
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "shl_product_catalog.json"


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {CATALOG_URL} ...")
    resp = httpx.get(CATALOG_URL, timeout=60.0)
    resp.raise_for_status()
    # The upstream file contains unescaped control characters, so parse leniently
    # (strict=False) rather than via resp.json(). We re-serialize cleanly below so
    # the committed file is valid, strict JSON that the service can load safely.
    data = json.loads(resp.text, strict=False)

    OUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Records may be a top-level list or wrapped in a dict — report either way.
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        # Find the first list value that looks like the catalog.
        records = next((v for v in data.values() if isinstance(v, list)), [])
    else:
        records = []

    print(f"Saved {OUT_PATH}")
    print(f"Record count: {len(records)}")
    if records:
        print("Sample record:")
        print(json.dumps(records[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
