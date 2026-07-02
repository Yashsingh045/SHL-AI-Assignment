"""Hybrid retrieval over the catalog: BM25 + dense embeddings fused with RRF.

- search(query, top_k)            -> records ranked by RRF of BM25 and cosine ranks.
- multi_search(aspects, top_k)    -> per-aspect search() fused with RRF across
                                     aspects, deduped by url (one aspect per skill/trait).

Index artifacts are built offline by scripts/build_index.py and committed under
data/index/. They are loaded lazily on the first query (so importing this module is
cheap, torch is only imported when a query actually runs, and scripts/build_index.py
can import tokenize/EMBED_MODEL_NAME from here before the index exists).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np
from rank_bm25 import BM25Okapi

from app import catalog

EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
RRF_K = 60  # Reciprocal Rank Fusion constant (standard default).

_INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "index"

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer that preserves tech markers (c++, c#, .net, g+)."""
    return [t.strip(".") or t for t in _TOKEN_RE.findall((text or "").lower())]


# --------------------------------------------------------------------------- #
# Index loading (at import)
# --------------------------------------------------------------------------- #
def _load_index():
    corpus_path = _INDEX_DIR / "bm25_corpus.json"
    emb_path = _INDEX_DIR / "embeddings.npy"
    urls_path = _INDEX_DIR / "doc_urls.json"
    if not (corpus_path.exists() and emb_path.exists() and urls_path.exists()):
        raise RuntimeError(
            f"Retrieval index missing under {_INDEX_DIR}. "
            "Run `python scripts/build_index.py` first."
        )

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    embeddings = np.load(emb_path).astype(np.float32)
    urls = json.loads(urls_path.read_text(encoding="utf-8"))

    # Align rows to catalog records by url (decouples from catalog load order).
    records = [catalog.get_by_url(u) for u in urls]
    if any(r is None for r in records):
        missing = [u for u, r in zip(urls, records) if r is None]
        raise RuntimeError(f"Index references urls not in catalog: {missing[:3]} ...")
    if not (len(corpus) == len(urls) == embeddings.shape[0] == len(records)):
        raise RuntimeError("Index artifacts are misaligned; rebuild the index.")

    return records, BM25Okapi(corpus), embeddings


_INDEX: Optional[tuple[list[dict], BM25Okapi, np.ndarray]] = None
_model = None  # lazily loaded SentenceTransformer


def _index() -> tuple[list[dict], BM25Okapi, np.ndarray]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _load_index()
    return _INDEX


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBED_MODEL_NAME)
    return _model


def warmup() -> None:
    """Eagerly load the index artifacts AND the embedding model.

    Called at service startup so the first /chat request doesn't pay index/model
    load latency (which would eat into the 30s per-call budget)."""
    _index()
    _get_model()


# --------------------------------------------------------------------------- #
# Ranking helpers
# --------------------------------------------------------------------------- #
def _ranks_from_scores(scores: np.ndarray) -> np.ndarray:
    """Return 0-based rank position for each doc (rank 0 = highest score)."""
    order = np.argsort(-scores, kind="stable")
    ranks = np.empty_like(order)
    ranks[order] = np.arange(len(order))
    return ranks


def search(query: str, top_k: int = 20) -> list[dict]:
    """Rank catalog records for a single query via RRF of BM25 and cosine ranks."""
    if not query or not query.strip():
        return []

    records, bm25, emb = _index()
    bm25_scores = np.asarray(bm25.get_scores(tokenize(query)), dtype=np.float32)

    q_emb = _get_model().encode(
        [query], normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)[0]
    cos_scores = emb @ q_emb  # embeddings are normalized -> cosine similarity

    bm25_ranks = _ranks_from_scores(bm25_scores)
    cos_ranks = _ranks_from_scores(cos_scores)

    rrf = 1.0 / (RRF_K + bm25_ranks + 1) + 1.0 / (RRF_K + cos_ranks + 1)
    top = np.argsort(-rrf, kind="stable")[:top_k]
    return [records[i] for i in top]


def multi_search(aspects: list[str], top_k: int = 20) -> list[dict]:
    """Fuse per-aspect search() results with RRF across aspects; dedupe by url.

    Use one aspect string per distinct skill/trait, e.g.
    ["Java programming knowledge test", "stakeholder communication personality"].
    """
    aspects = [a for a in (aspects or []) if a and a.strip()]
    if not aspects:
        return []
    if len(aspects) == 1:
        return search(aspects[0], top_k=top_k)

    # Retrieve deeper per aspect than top_k so fusion has room to promote items.
    depth = max(top_k, 30)
    fused: dict[str, float] = {}
    best_record: dict[str, dict] = {}
    for aspect in aspects:
        ranked = search(aspect, top_k=depth)
        for rank, rec in enumerate(ranked):
            url = rec["url"]
            fused[url] = fused.get(url, 0.0) + 1.0 / (RRF_K + rank + 1)
            best_record.setdefault(url, rec)

    order = sorted(fused, key=lambda u: fused[u], reverse=True)
    return [best_record[u] for u in order[:top_k]]
