"""Чиста та швидка реалізація Okapi BM25 для українських та англійських банківських текстів.

Використовується для лексичного пошуку за точними артикулами, номерами постанов (351, 352),
ІПН позичальника та специфічними фінансовими абревіатурами (PTI, DTI, LTV, ECL).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

# Базові стоп-слова, які не несуть пошукового змісту
STOPWORDS = {
    "і", "й", "та", "що", "як", "для", "на", "у", "в", "до", "по", "за", "з", "із",
    "від", "під", "про", "або", "чи", "якщо", "це", "цей", "ця", "ці", "того", "тому",
    "при", "через", "бути", "може", "має", "є", "який", "яка", "яке", "які", "так", "ні"
}


def tokenize(text: str) -> list[str]:
    """Нормалізація та токенізація тексту для BM25."""
    # Знаходимо слова, номери та ідентифікатори (наприклад, "351", "pti", "фоп")
    tokens = re.findall(r"[a-zA-Zа-яА-ЯіїєґІЇЄҐ0-9_\-]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in STOPWORDS]


@dataclass
class BM25Document:
    id: str
    tokens: list[str]
    length: int


class BM25Index:
    """Індекс Okapi BM25."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: list[BM25Document] = []
        self.doc_freqs: dict[str, int] = Counter()
        self.avg_doc_len: float = 0.0
        self.doc_count: int = 0

    def index(self, doc_ids: Sequence[str], texts: Sequence[str]) -> None:
        """Побудова зворотного індексу за списком документів."""
        self.docs = []
        self.doc_freqs = Counter()
        total_len = 0

        for doc_id, text in zip(doc_ids, texts):
            tokens = tokenize(text)
            doc_len = len(tokens)
            total_len += doc_len
            
            # Збільшуємо частоту документа для унікальних слів
            unique_terms = set(tokens)
            for term in unique_terms:
                self.doc_freqs[term] += 1

            self.docs.append(BM25Document(id=doc_id, tokens=tokens, length=doc_len))

        self.doc_count = len(self.docs)
        self.avg_doc_len = total_len / self.doc_count if self.doc_count > 0 else 0.0

    def _idf(self, term: str) -> float:
        """Розрахунок згладженого Inverse Document Frequency."""
        df = self.doc_freqs.get(term, 0)
        if df == 0:
            return 0.0
        # Стандартна формула Lucene / Okapi
        return math.log(1.0 + (self.doc_count - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> list[tuple[str, float]]:
        """Оцінка релевантності запиту для кожного документа в індексі.
        
        Повертає список кортежів `(doc_id, bm25_score)`, відсортований за спаданням.
        """
        query_tokens = tokenize(query)
        if not query_tokens or self.doc_count == 0:
            return [(d.id, 0.0) for d in self.docs]

        scores: list[float] = [0.0] * self.doc_count

        for q_term in query_tokens:
            idf = self._idf(q_term)
            if idf <= 0.0:
                continue

            for idx, doc in enumerate(self.docs):
                if q_term not in doc.tokens:
                    continue
                tf = doc.tokens.count(q_term)
                # BM25 TF формула з нормуванням довжини
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc.length / self.avg_doc_len))
                term_score = idf * (tf * (self.k1 + 1.0)) / denom
                scores[idx] += term_score

        results = [(self.docs[i].id, scores[i]) for i in range(self.doc_count)]
        results.sort(key=lambda x: x[1], reverse=True)
        return results
