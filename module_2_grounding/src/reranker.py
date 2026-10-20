"""Cross-Encoder Reranker: другий етап двохетапного пошуку (Two-Stage Retrieval).

Приймає кандидатів (top-K) з першого етапу та переранжує їх
через детальне зіставлення кожного фрагмента із запитом.
"""

from __future__ import annotations

import re
from typing import Sequence

from .chunking import Chunk


def _calculate_cross_attention_heuristic(query: str, chunk_text: str) -> float:
    """Евристична оцінка глибинного зіставлення термінів, чисел та контексту запиту."""
    q_words = re.findall(r"[a-zA-Zа-яА-ЯіїєґІЇЄҐ0-9]+", query.lower())
    c_words = set(re.findall(r"[a-zA-Zа-яА-ЯіїєґІЇЄҐ0-9]+", chunk_text.lower()))

    if not q_words or not c_words:
        return 0.0

    # 1. Точний збіг ключових термінів
    matched_words = [w for w in q_words if w in c_words]
    overlap_ratio = len(matched_words) / len(q_words)

    # 2. Бонус за наявність специфічних числових обмежень або дат
    numbers_in_query = set(re.findall(r"\d+", query))
    numbers_in_chunk = set(re.findall(r"\d+", chunk_text))
    num_match = len(numbers_in_query & numbers_in_chunk) * 0.25

    # 3. Бонус за прямі винятки («виняток», «не менше», «заборонено», «крім»)
    exception_keywords = {"виняток", "винятком", "крім", "заборонено", "підлягають", "обов'язково"}
    exception_match = 0.2 if any(k in chunk_text.lower() for k in exception_keywords) else 0.0

    return overlap_ratio + num_match + exception_match


class Reranker:
    """Cross-Encoder / LLM Reranker."""

    def __init__(self, use_llm: bool = False) -> None:
        self.use_llm = use_llm

    def rerank(
        self,
        query: str,
        candidates: Sequence[tuple[Chunk, float]],
        top_n: int = 3,
    ) -> list[tuple[Chunk, float]]:
        """Переранжування пулу кандидатів (наприклад, top-10 -> top-3)."""
        scored_candidates: list[tuple[Chunk, float]] = []

        for chunk, base_score in candidates:
            cross_score = _calculate_cross_attention_heuristic(query, chunk.full_content)
            # Комбінований скор: база + точне зіставлення
            final_score = (base_score * 0.3) + (cross_score * 0.7)
            scored_candidates.append((chunk, final_score))

        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        return scored_candidates[:top_n]
