"""Hybrid retrieval over the catalog: BM25 + dense embeddings fused with RRF.

- search(query, top_k)            -> records ranked by RRF of BM25 and cosine ranks.
- multi_search(aspects, top_k)    -> per-aspect search() fused with RRF across
                                     aspects, deduped by url (one aspect per skill/trait).

Query embeddings use fastembed (ONNX runtime) with all-MiniLM-L6-v2 — the SAME model
weights and embedding space as the committed doc matrix (built with sentence-transformers;
verified cosine 1.0000), but WITHOUT importing PyTorch, so the service fits the Render
512 MB free tier. Index artifacts are built offline by scripts/build_index.py and
committed under data/index/; they load lazily on the first query.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np
from rank_bm25 import BM25Okapi

from app import catalog

# fastembed's registry name for all-MiniLM-L6-v2 (same weights as the doc matrix).
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
RRF_K = 60  # Reciprocal Rank Fusion constant (standard default).

_ROOT = Path(__file__).resolve().parent.parent
_INDEX_DIR = _ROOT / "data" / "index"
# ONNX model cache: populated at BUILD time (see render.yaml), read at runtime.
_CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH") or str(_ROOT / ".fastembed_cache")

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
        from fastembed import TextEmbedding

        _model = TextEmbedding(EMBED_MODEL_NAME, cache_dir=_CACHE_DIR)
    return _model


def _embed_query(query: str) -> np.ndarray:
    """Embed one query with fastembed -> normalized 384-d float32 (matches the doc matrix)."""
    vec = next(iter(_get_model().embed([query])))
    return np.asarray(vec, dtype=np.float32)


def warmup() -> None:
    """Eagerly load the index artifacts AND the embedding model (ONNX session).

    Called at service startup so the first /chat request doesn't pay index/model
    load latency (which would eat into the 30s per-call budget)."""
    _index()
    _embed_query("warmup")


# --------------------------------------------------------------------------- #
# Ranking helpers
# --------------------------------------------------------------------------- #
def _ranks_from_scores(scores: np.ndarray) -> np.ndarray:
    """Return 0-based rank position for each doc (rank 0 = highest score)."""
    order = np.argsort(-scores, kind="stable")
    ranks = np.empty_like(order)
    ranks[order] = np.arange(len(order))
    return ranks


def _apply_boost(records: list[dict], boost_terms: Optional[list[str]]) -> list[dict]:
    """Stable-promote records whose NAME contains a boost term to the front,
    preserving the original relative order within each group (c3 keyword boost)."""
    terms = [t.lower() for t in (boost_terms or []) if t and len(t) >= 3]
    if not terms:
        return records
    def hit(rec):
        name = rec["name"].lower()
        return any(t in name for t in terms)
    boosted = [r for r in records if hit(r)]
    rest = [r for r in records if not hit(r)]
    return boosted + rest


def search(query: str, top_k: int = 20,
           boost_terms: Optional[list[str]] = None) -> list[dict]:
    """Rank catalog records for a single query via RRF of BM25 and cosine ranks.
    boost_terms (optional): records whose name contains a term are promoted."""
    if not query or not query.strip():
        return []

    records, bm25, emb = _index()
    bm25_scores = np.asarray(bm25.get_scores(tokenize(query)), dtype=np.float32)

    q_emb = _embed_query(query)  # fastembed: normalized 384-d, same space as `emb`
    cos_scores = emb @ q_emb  # embeddings are normalized -> cosine similarity

    bm25_ranks = _ranks_from_scores(bm25_scores)
    cos_ranks = _ranks_from_scores(cos_scores)

    rrf = 1.0 / (RRF_K + bm25_ranks + 1) + 1.0 / (RRF_K + cos_ranks + 1)
    # Take a bit deeper than top_k so a name-boost can pull a match into the top_k.
    order = np.argsort(-rrf, kind="stable")[: max(top_k, top_k + 10)]
    ranked = [records[i] for i in order]
    return _apply_boost(ranked, boost_terms)[:top_k]


def multi_search(aspects: list[str], top_k: int = 20,
                 boost_terms: Optional[list[str]] = None) -> list[dict]:
    """Fuse per-aspect search() results with RRF across aspects; dedupe by url.

    Use one aspect string per distinct skill/trait, e.g.
    ["Java programming knowledge test", "stakeholder communication personality"].
    boost_terms (optional): records whose name contains a term are promoted (c3).
    """
    aspects = [a for a in (aspects or []) if a and a.strip()]
    if not aspects:
        return []
    if len(aspects) == 1:
        return search(aspects[0], top_k=top_k, boost_terms=boost_terms)

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
    ranked = [best_record[u] for u in order]
    return _apply_boost(ranked, boost_terms)[:top_k]
