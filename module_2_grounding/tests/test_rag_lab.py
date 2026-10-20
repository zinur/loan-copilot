"""Тести для RAG Optimization Ladder Lab (Module 2 Grounding)."""

from __future__ import annotations

import pytest

from module_2_grounding.src.bm25 import BM25Index, tokenize
from module_2_grounding.src.chunking import (
    contextual_chunk_document,
    naive_chunk_document,
    semantic_chunk_document,
)
from module_2_grounding.src.dense_retriever import DenseRetriever
from module_2_grounding.src.hybrid_search import (
    HybridSearchPipeline,
    reciprocal_rank_fusion,
)
from module_2_grounding.src.rag_lab import load_corpus
from module_2_grounding.src.reranker import Reranker


def test_load_corpus():
    corpus = load_corpus()
    assert "POL-LOAN-01" in corpus
    assert "SOP-CREDIT-02" in corpus
    assert "REG-NBU-351" in corpus
    assert len(corpus["POL-LOAN-01"]) > 100


def test_chunking_strategies():
    sample_text = (
        "## Розділ 1. Загальні положення\n"
        "Кредит надається резидентам України від 21 року.\n\n"
        "## Розділ 2. Винятки\n"
        "Для беззаставних кредитів понад 100 тисяч грн потрібен стаж 12 місяців."
    )

    naive_chunks = naive_chunk_document("TEST-01", sample_text, chunk_size=20, overlap=5)
    assert len(naive_chunks) >= 2

    sem_chunks = semantic_chunk_document("TEST-01", sample_text, max_tokens=100)
    assert len(sem_chunks) == 2
    assert "Розділ 1" in sem_chunks[0].text
    assert "Розділ 2" in sem_chunks[1].text

    ctx_chunks = contextual_chunk_document("TEST-01", sample_text, max_tokens=100)
    assert len(ctx_chunks) == 2
    assert "Документ:" in ctx_chunks[0].context_prefix


def test_bm25_scoring():
    index = BM25Index()
    docs = [
        "Постанова Правління Національного банку України № 351 про кредитний ризик.",
        "Порядок розрахунку коефіцієнта навантаження PTI для споживчих кредитів.",
        "Постанова Правління НБУ № 352 про обов'язкове резервування коштів.",
    ]
    doc_ids = ["doc1", "doc2", "doc3"]
    index.index(doc_ids, docs)

    # Запит із точним номером 351
    scores = index.score("Постанова 351")
    assert scores[0][0] == "doc1"
    assert scores[0][1] > scores[1][1]

    # Запит із точним терміном PTI
    scores_pti = index.score("розрахунок PTI")
    assert scores_pti[0][0] == "doc2"


def test_hybrid_search_rrf():
    pipeline = HybridSearchPipeline(use_api=False)
    corpus = load_corpus()
    chunks = semantic_chunk_document("POL-LOAN-01", corpus["POL-LOAN-01"])
    pipeline.index_chunks(chunks)

    results = pipeline.search_hybrid("ФОП беззаставний кредит 12 місяців", top_k=3)
    assert len(results) > 0
    top_chunk = results[0][0]
    assert "POL-LOAN-01" in top_chunk.doc_id


def test_reranker():
    pipeline = HybridSearchPipeline(use_api=False)
    corpus = load_corpus()
    chunks = semantic_chunk_document("SOP-CREDIT-02", corpus["SOP-CREDIT-02"])
    pipeline.index_chunks(chunks)

    candidates = pipeline.search_hybrid("граничний PTI та скор для Tier A", top_k=5)
    reranker = Reranker(use_llm=False)
    reranked = reranker.rerank("граничний PTI та скор для Tier A", candidates, top_n=2)

    assert len(reranked) == 2
    assert "SOP-CREDIT-02" in reranked[0][0].doc_id
