"""Модуль стратегій чанкінгу банківських регламентів.

Демонструє різницю між:
1. Наївним чанкінгом фіксованого розміру (Fixed-Size / Naive)
2. Семантичним чанкінгом зі збереженням структури статей (Semantic)
3. Контекстним чанкінгом (Contextual Chunking за методологією Anthropic)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str
    section: str
    token_count_approx: int
    context_prefix: str = ""

    @property
    def full_content(self) -> str:
        """Повний вміст для ембедінгу та генерації."""
        if self.context_prefix:
            return f"[{self.context_prefix}]\n{self.text}"
        return self.text


def _estimate_tokens(text: str) -> int:
    """Приблизна оцінка токенів для змішаного UA/EN тексту (~4 символи на токен)."""
    return max(1, len(text) // 4)


def naive_chunk_document(doc_id: str, text: str, chunk_size: int = 150, overlap: int = 30) -> list[Chunk]:
    """Наївне розбиття тексту на фіксовані шматки без урахування структури розділів.
    
    Типовий результат: розрізає формули навпіл, відокремлює винятки від базових правил.
    """
    words = text.split()
    chunks: list[Chunk] = []
    
    # Конвертуємо розмір з токенів у слова (~1 токен ≈ 0.75 слова в укр. мові)
    words_per_chunk = max(10, int(chunk_size * 0.75))
    overlap_words = max(0, int(overlap * 0.75))
    step = max(1, words_per_chunk - overlap_words)

    chunk_idx = 1
    for i in range(0, len(words), step):
        chunk_words = words[i : i + words_per_chunk]
        chunk_text = " ".join(chunk_words).strip()
        if not chunk_text:
            continue
        chunks.append(
            Chunk(
                id=f"{doc_id}-naive-{chunk_idx:03d}",
                doc_id=doc_id,
                text=chunk_text,
                section="unknown",
                token_count_approx=_estimate_tokens(chunk_text),
            )
        )
        chunk_idx += 1
        if i + words_per_chunk >= len(words):
            break

    return chunks


def semantic_chunk_document(doc_id: str, text: str, max_tokens: int = 500) -> list[Chunk]:
    """Семантичне розбиття зі збереженням цілісності статей та підпунктів.
    
    Шукає заголовки (##, ###) та пункти (наприклад, 4.2, 4.2.1), тримаючи правило і виняток разом.
    """
    lines = text.split("\n")
    chunks: list[Chunk] = []
    
    current_section = "Вступ"
    current_buffer: list[str] = []
    chunk_idx = 1

    for line in lines:
        stripped = line.strip()
        # Детекція заголовків розділів
        if stripped.startswith("## ") or stripped.startswith("### "):
            if current_buffer:
                buffer_text = "\n".join(current_buffer).strip()
                if buffer_text:
                    chunks.append(
                        Chunk(
                            id=f"{doc_id}-sem-{chunk_idx:03d}",
                            doc_id=doc_id,
                            text=buffer_text,
                            section=current_section,
                            token_count_approx=_estimate_tokens(buffer_text),
                        )
                    )
                    chunk_idx += 1
                current_buffer = []
            current_section = stripped.lstrip("#").strip()
            current_buffer.append(line)
            continue

        current_buffer.append(line)

        # Якщо буфер перевищує ліміт — фіксуємо чанк по межі абзацу
        if _estimate_tokens("\n".join(current_buffer)) >= max_tokens:
            buffer_text = "\n".join(current_buffer).strip()
            chunks.append(
                Chunk(
                    id=f"{doc_id}-sem-{chunk_idx:03d}",
                    doc_id=doc_id,
                    text=buffer_text,
                    section=current_section,
                    token_count_approx=_estimate_tokens(buffer_text),
                )
            )
            chunk_idx += 1
            current_buffer = []

    if current_buffer:
        buffer_text = "\n".join(current_buffer).strip()
        if buffer_text:
            chunks.append(
                Chunk(
                    id=f"{doc_id}-sem-{chunk_idx:03d}",
                    doc_id=doc_id,
                    text=buffer_text,
                    section=current_section,
                    token_count_approx=_estimate_tokens(buffer_text),
                )
            )

    return chunks


def contextual_chunk_document(doc_id: str, text: str, max_tokens: int = 400) -> list[Chunk]:
    """Контекстний чанкінг (Anthropic Contextual Retrieval).
    
    Бере семантичні чанки та додає короткий префікс про документ, розділ та предмет регулювання.
    """
    base_chunks = semantic_chunk_document(doc_id, text, max_tokens=max_tokens)
    
    # Визначаємо глобальний контекст документа з перших рядків
    doc_title = doc_id
    first_lines = text.split("\n")[:4]
    for fl in first_lines:
        if fl.strip().startswith("# "):
            doc_title = fl.strip().lstrip("#").strip()
            break

    for chunk in base_chunks:
        chunk.context_prefix = f"Документ: {doc_title} | Розділ: {chunk.section}"
    
    return base_chunks
