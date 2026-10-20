"""Гібридний пошук: об'єднання BM25 (лексика) та Dense (семантика).

Використовує Reciprocal Rank Fusion (RRF) — стандарт enterprise RAG,
оскільки не вимагає складної нормалізації різних шкал оцінок.
"""

from __future__ import annotations

from typing import Sequence

from .bm25 import BM25Index
from .chunking import Chunk
from .dense_retriever import DenseRetriever


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Об'єднання кількох списків ранжування за алгоритмом RRF.
    
    RRF_score(doc) = SUM( 1.0 / (k + rank_i(doc)) )
    де k — згладжуюча константа (зазвичай 60).
    """
    rrf_scores: dict[str, float] = {}

    for r_list in ranked_lists:
        for rank, (doc_id, _) in enumerate(r_list, start=1):
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = 0.0
            rrf_scores[doc_id] += 1.0 / (k + rank)

    sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results


class HybridSearchPipeline:
    """Гібридний RAG-пайплайн."""

    def __init__(self, use_api: bool = True) -> None:
        self.bm25 = BM25Index()
        self.dense = DenseRetriever(use_api=use_api)
        self.chunks: dict[str, Chunk] = {}

    def index_chunks(self, chunks: Sequence[Chunk]) -> None:
        """Індексація набору чанків в обох рушіях."""
        self.chunks = {c.id: c for c in chunks}
        chunk_ids = [c.id for c in chunks]
        texts = [c.full_content for c in chunks]

        # Індексуємо в BM25
        self.bm25.index(chunk_ids, texts)
        # Індексуємо у Dense
        self.dense.index(chunk_ids, texts)

    def search_dense(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """Тільки векторний пошук."""
        scores = self.dense.score(query)[:top_k]
        return [(self.chunks[doc_id], sc) for doc_id, sc in scores]

    def search_bm25(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """Тільки лексичний пошук BM25."""
        scores = self.bm25.score(query)[:top_k]
        return [(self.chunks[doc_id], sc) for doc_id, sc in scores]

    def search_hybrid(self, query: str, top_k: int = 5, rrf_k: int = 60) -> list[tuple[Chunk, float]]:
        """Гібридний пошук (BM25 + Dense) через Reciprocal Rank Fusion."""
        bm25_scores = self.bm25.score(query)
        dense_scores = self.dense.score(query)

        fused_scores = reciprocal_rank_fusion([bm25_scores, dense_scores], k=rrf_k)[:top_k]
        return [(self.chunks[doc_id], sc) for doc_id, sc in fused_scores]
