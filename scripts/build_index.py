"""Build and persist the retrieval index from the committed catalog.

Artifacts (data/index/), all derived offline from data/shl_product_catalog.json:
  - bm25_corpus.json : tokenized search_doc per record (list of token lists)
  - embeddings.npy   : L2-normalized dense embeddings (float32, [N, D])
  - doc_urls.json    : record urls in row order (aligns both indexes to records)
  - meta.json        : model name, record count, embedding dim

Run:  python scripts/build_index.py

The service NEVER rebuilds at runtime and NEVER fetches the catalog URL — it loads
these committed artifacts. Re-run this script only when the catalog changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.catalog import all_records
from app.retrieval import EMBED_MODEL_NAME, tokenize

INDEX_DIR = _ROOT / "data" / "index"


def main() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    records = all_records()
    docs = [r["search_doc"] for r in records]
    urls = [r["url"] for r in records]
    print(f"Indexing {len(records)} catalog records ...")

    # --- BM25 corpus (tokenized; BM25Okapi is rebuilt cheaply at query time) ---
    corpus = [tokenize(d) for d in docs]
    (INDEX_DIR / "bm25_corpus.json").write_text(
        json.dumps(corpus, ensure_ascii=False), encoding="utf-8"
    )

    # --- Dense embeddings (normalized so cosine == dot product) ---
    from sentence_transformers import SentenceTransformer

    print(f"Loading embedding model {EMBED_MODEL_NAME} ...")
    model = SentenceTransformer(EMBED_MODEL_NAME)
    emb = model.encode(
        docs, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=True
    ).astype(np.float32)
    np.save(INDEX_DIR / "embeddings.npy", emb)

    (INDEX_DIR / "doc_urls.json").write_text(
        json.dumps(urls, ensure_ascii=False), encoding="utf-8"
    )
    (INDEX_DIR / "meta.json").write_text(
        json.dumps(
            {"model": EMBED_MODEL_NAME, "count": len(records), "dim": int(emb.shape[1])},
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Wrote artifacts to {INDEX_DIR}")
    print(f"  bm25_corpus.json : {len(corpus)} docs")
    print(f"  embeddings.npy   : {emb.shape} {emb.dtype}")
    print(f"  doc_urls.json    : {len(urls)} urls")


if __name__ == "__main__":
    main()
