"""Векторний семантичний ретрівер (Dense Retrieval).

Підтримує:
1. Хмарні ембедінги через DIAL / OpenAI (якщо задано змінні оточення)
2. Локальний детермінований семантичний fallback для офлайн-роботи в лабораторній
"""

from __future__ import annotations

import math
import os
import re
from typing import Sequence


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Обчислення косинусної подібності між двома векторами."""
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


class LocalSemanticEncoder:
    """Легкий детермінований семантичний енкодер для тестування без API-викликів.
    
    Створює 256-вимірний вектор на основі n-грамів слів та семантичних банківських концептів.
    """

    DIM = 256

    def __init__(self) -> None:
        # Семантичні якорі банківського домену для проекції
        self.concepts = {
            "credit": ["кредит", "позика", "заборгованість", "ліміт", "овердрафт", "тіло", "loan"],
            "income": ["дохід", "зарплата", "виручка", "прибуток", "обіг", "надходження", "income"],
            "risk": ["ризик", "дефолт", "прострочка", "збитки", "відмова", "неплатоспроможність", "risk"],
            "fop": ["фоп", "підприємець", "бізнес", "спрощена", "єсв", "реєстрація", "діяльність"],
            "terms": ["відсоток", "ставка", "ануїтет", "термін", "строк", "місяць", "графік", "apr", "pti"],
            "nbu": ["нбу", "постанова", "регламент", "вимога", "закон", "стаття", "норматив", "351"],
            "docs": ["паспорт", "іпн", "довідка", "витяг", "згода", "договір", "ідентифікація"],
        }

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.DIM
        tokens = re.findall(r"[a-zA-Zа-яА-ЯіїєґІЇЄҐ0-9]+", text.lower())

        for idx, token in enumerate(tokens):
            # Хешування слова у вимір вектора
            h = hash(token) % self.DIM
            vector[h] += 1.0

            # Підсилення за семантичними концептами
            for c_idx, (c_name, words) in enumerate(self.concepts.items()):
                if any(w in token for w in words):
                    concept_slot = (c_idx * 30) % self.DIM
                    vector[concept_slot] += 2.5

        # L2-нормалізація вектора
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0.0:
            vector = [x / norm for x in vector]
        return vector


class DenseRetriever:
    """Семантичний векторний ретрівер."""

    def __init__(self, use_api: bool = True) -> None:
        self.use_api = use_api
        self.local_encoder = LocalSemanticEncoder()
        self.doc_ids: list[str] = []
        self.doc_embeddings: list[list[float]] = []

    def _get_api_embedding(self, text: str) -> list[float] | None:
        """Спроба отримати ембедінг через DIAL або OpenAI."""
        if not self.use_api:
            return None

        # Перевірка наявності ключів
        api_key = os.getenv("DIAL_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            from openai import OpenAI

            base_url = os.getenv("DIAL_API_URL")
            if base_url:
                api_version = os.getenv("DIAL_API_VERSION", "2024-10-21")
                client = OpenAI(
                    api_key=api_key,
                    base_url=base_url.rstrip("/"),
                    default_query={"api-version": api_version},
                    default_headers={"Api-Key": api_key},
                )
                model = os.getenv("DIAL_EMBEDDING_MODEL", "text-embedding-3-small-1")
            else:
                client = OpenAI(api_key=api_key)
                model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

            resp = client.embeddings.create(input=[text[:2000]], model=model)
            return resp.data[0].embedding
        except Exception:
            # При будь-якій помилці мережі/ключа — прозорий fallback на локальний енкодер
            return None

    def embed(self, text: str) -> list[float]:
        """Ембедінг тексту (API або local fallback)."""
        emb = self._get_api_embedding(text)
        if emb is not None:
            return emb
        return self.local_encoder.encode(text)

    def index(self, doc_ids: Sequence[str], texts: Sequence[str]) -> None:
        """Індексація документів."""
        self.doc_ids = list(doc_ids)
        self.doc_embeddings = [self.embed(t) for t in texts]

    def score(self, query: str) -> list[tuple[str, float]]:
        """Оцінка семантичної подібності запиту до кожного документа.
        
        Повертає список кортежів `(doc_id, cosine_score)`, відсортований за спаданням.
        """
        q_emb = self.embed(query)
        scores: list[tuple[str, float]] = []

        for doc_id, doc_emb in zip(self.doc_ids, self.doc_embeddings):
            sim = _cosine_similarity(q_emb, doc_emb)
            scores.append((doc_id, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores
