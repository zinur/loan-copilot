"""Лабораторний стенд W2: RAG Optimization Ladder.

Практичний лабораторний стенд для AI-архітекторів:
- Експеримент 1: Битва розмірів чанків (150 vs 500 vs 1200 токенів)
- Експеримент 2: Дуель пошуку (Dense vs BM25 vs Hybrid RRF)
- Експеримент 3: Reranker (як змінюється ранг цільового чанка; евристика, що імітує cross-encoder)
- Експеримент 4: Citation & Refusal Gate (відмова, якщо в політиках немає підтверджуючої норми)

Усі таблиці й висновки обчислюються з реальної видачі пошуку; готових відповідей
чи захардкоджених метрик тут немає. BM25 — справжній; «Dense» тут — локальний
детермінований енкодер (не нейромережа), а reranker — евристика термінів і чисел.

Запуск:
    python -m module_2_grounding.src.rag_lab
    python -m module_2_grounding.src.rag_lab --exp 1
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .chunking import (
    Chunk,
    contextual_chunk_document,
    naive_chunk_document,
    semantic_chunk_document,
)
from .hybrid_search import HybridSearchPipeline
from .reranker import Reranker

console = Console()

POLICIES_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"


def load_corpus() -> dict[str, str]:
    """Завантаження текстів банківських регламентів."""
    docs: dict[str, str] = {}
    for filename in ["POL-LOAN-01.md", "SOP-CREDIT-02.md", "REG-NBU-351.md"]:
        filepath = POLICIES_DIR / filename
        if filepath.exists():
            docs[filename.replace(".md", "")] = filepath.read_text(encoding="utf-8")
        else:
            rprint(f"[bold red]Помилка:[/bold red] Файл {filepath} не знайдено.")
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Допоміжні функції
# ─────────────────────────────────────────────────────────────────────────────


def _rank_of(results, predicate) -> int | None:
    """1-based ранг першого чанка, що задовольняє умову; None, якщо його немає у видачі."""
    for i, (chunk, _score) in enumerate(results, start=1):
        if predicate(chunk):
            return i
    return None


def _fmt_rank(rank: int | None, depth: int) -> str:
    return f"#{rank}" if rank is not None else f"поза Top-{depth}"


def _ok(flag: bool, yes: str, no: str) -> str:
    return f"[green]{yes}[/green]" if flag else f"[red]{no}[/red]"


def _is_fop_exception(chunk: Chunk) -> bool:
    return "4.2.1" in chunk.text or "12 місяців" in chunk.text


def _is_tier_matrix(chunk: Chunk) -> bool:
    return chunk.doc_id == "SOP-CREDIT-02" and "Tier A" in chunk.text


# ──────────────────────────────────────────────
# ЕКСПЕРИМЕНТ 1: Розмір чанків (Chunk Size & Overlap)
# ──────────────────────────────────────────────


def run_experiment_1_chunking(corpus: dict[str, str]) -> None:
    rprint("\n" + "=" * 75)
    rprint("[bold cyan]ЕКСПЕРИМЕНТ 1 · Розмір чанків (Chunk Size)[/bold cyan]")
    rprint("=" * 75)
    query = "ФОП без застави 150 000 грн строк реєстрації 8 місяців"
    rprint(f"[dim]Запит: «{query}»\nОчікуваний фрагмент: виняток для ФОП (POL-LOAN-01 §4.2.1, 12 місяців)[/dim]\n")

    pol_text = corpus.get("POL-LOAN-01", "")
    configs = [
        ("1. Naive (120 tokens)", naive_chunk_document("POL-LOAN-01", pol_text, chunk_size=120, overlap=15)),
        ("2. Semantic (500 tokens)", semantic_chunk_document("POL-LOAN-01", pol_text, max_tokens=500)),
    ]

    table = Table(title="BM25 Top-1 для різних стратегій чанкінгу", show_lines=True)
    table.add_column("Стратегія", style="bold", width=24)
    table.add_column("Знайдений фрагмент (Top-1)", width=46)
    table.add_column("Чанків", justify="center")
    table.add_column("Виняток ФОП у Top-1", width=20)

    outcomes: list[bool] = []
    for name, chunks in configs:
        pipe = HybridSearchPipeline(use_api=False)
        pipe.index_chunks(chunks)
        res = pipe.search_bm25(query, top_k=1)
        top = res[0][0] if res else None
        found = bool(top and _is_fop_exception(top))
        outcomes.append(found)
        text = top.text if top else "Не знайдено"
        table.add_row(
            name,
            text[:200] + ("..." if len(text) > 200 else ""),
            str(len(chunks)),
            _ok(found, "ТАК (є 12 місяців / §4.2.1)", "НІ (правило-виняток не потрапило)"),
        )
    console.print(table)

    naive_found, sem_found = outcomes
    if sem_found and not naive_found:
        verdict = "Naive-чанкінг не підняв виняток у Top-1, а semantic підняв: розрив статті губить виняток."
    elif sem_found and naive_found:
        verdict = "Обидві стратегії знайшли виняток у Top-1 на цьому корпусі та запиті (різниці немає)."
    elif naive_found and not sem_found:
        verdict = "Naive знайшов виняток, semantic ні: на цьому корпусі результат протилежний очікуваному."
    else:
        verdict = "Жодна стратегія не підняла виняток у Top-1 для цього запиту."
    rprint(Panel(f"[bold]Результат:[/bold] {verdict}", border_style="cyan"))


# ──────────────────────────────────────────────
# ЕКСПЕРИМЕНТ 2: Dense vs BM25 vs Hybrid RRF
# ──────────────────────────────────────────────


def run_experiment_2_retrieval(corpus: dict[str, str]) -> None:
    rprint("\n" + "=" * 75)
    rprint("[bold cyan]ЕКСПЕРИМЕНТ 2 · Dense vs BM25 vs Hybrid RRF[/bold cyan]")
    rprint("=" * 75)

    all_chunks: list[Chunk] = []
    for doc_id, text in corpus.items():
        all_chunks.extend(semantic_chunk_document(doc_id, text, max_tokens=400))
    pipeline = HybridSearchPipeline(use_api=False)
    pipeline.index_chunks(all_chunks)

    tests = [
        ("2.1 Точний нормативний запит", "Постанова № 351 критерії дефолту споживчих позик",
         lambda c: c.doc_id == "REG-NBU-351", "чанк із REG-NBU-351"),
        ("2.2 Розмовний парафраз", "як банк списує борги якщо позичальник потрапив у форс-мажор на війні",
         lambda c: "списання" in c.text.lower() or "канікул" in c.text.lower(), "чанк про списання/канікули"),
    ]
    for title, query, predicate, target_name in tests:
        rprint(f"\n[bold yellow]{title}:[/bold yellow] «{query}»  [dim](ціль: {target_name})[/dim]")
        table = Table(show_lines=True)
        table.add_column("Метод", style="bold")
        table.add_column("Top-1", style="cyan")
        table.add_column("Ранг цілі в Top-3")
        for name, fn in [("Dense (локальний енкодер)", pipeline.search_dense),
                         ("BM25 (лексичний)", pipeline.search_bm25),
                         ("Hybrid (RRF)", pipeline.search_hybrid)]:
            res = fn(query, top_k=3)
            top = f"{res[0][0].doc_id} · {res[0][0].section[:30]}" if res else "—"
            rank = _rank_of(res, predicate)
            table.add_row(name, top, _ok(rank == 1, f"#{rank}", _fmt_rank(rank, 3)))
        console.print(table)
    rprint(Panel("[dim]Колонка «Ранг цілі» обчислена з реальної видачі; висновки про переваги методів "
                 "робіть зі своїх запитів, а не з одного прикладу.[/dim]", border_style="green"))


# ──────────────────────────────────────────────
# ЕКСПЕРИМЕНТ 3: Reranker
# ──────────────────────────────────────────────


def run_experiment_3_reranker(corpus: dict[str, str]) -> None:
    rprint("\n" + "=" * 75)
    rprint("[bold cyan]ЕКСПЕРИМЕНТ 3 · Reranker (евристика, що імітує cross-encoder)[/bold cyan]")
    rprint("=" * 75)
    query = "ставки кредитування та матриця рішень за рейтингом позичальника"
    rprint(f"[dim]Запит: «{query}»\nЦіль: матриця 4-Tier (SOP-CREDIT-02, Tier A/B/C)[/dim]\n")

    all_chunks: list[Chunk] = []
    for doc_id, text in corpus.items():
        all_chunks.extend(semantic_chunk_document(doc_id, text, max_tokens=250))
    pipeline = HybridSearchPipeline(use_api=False)
    pipeline.index_chunks(all_chunks)

    depth = 10
    candidates = pipeline.search_hybrid(query, top_k=depth)
    reranked = Reranker().rerank(query, candidates, top_n=depth)

    table = Table(title="Топ-5 до і після reranker", show_lines=True)
    table.add_column("Позиція", justify="center")
    table.add_column("До (Hybrid)")
    table.add_column("Після (Reranker)")
    for i in range(min(5, len(candidates))):
        b, a = candidates[i][0], reranked[i][0]
        mark_b = " ◄ ціль" if _is_tier_matrix(b) else ""
        mark_a = " ◄ ціль" if _is_tier_matrix(a) else ""
        table.add_row(f"#{i + 1}", f"{b.doc_id} · {b.section[:24]}{mark_b}", f"{a.doc_id} · {a.section[:24]}{mark_a}")
    console.print(table)

    before = _rank_of(candidates, _is_tier_matrix)
    after = _rank_of(reranked, _is_tier_matrix)
    if before is None:
        msg = f"Цільова матриця не потрапила в Top-{depth} кандидатів."
    elif after is not None and before is not None and after < before:
        msg = f"Ранг цільового чанка: {_fmt_rank(before, depth)} → {_fmt_rank(after, depth)} (покращення)."
    elif after == before:
        msg = f"Ранг цільового чанка не змінився: {_fmt_rank(before, depth)}."
    else:
        msg = f"Ранг цільового чанка погіршився: {_fmt_rank(before, depth)} → {_fmt_rank(after, depth)}."
    in_prompt = "потрапляє" if (after is not None and after <= 3) else "НЕ потрапляє"
    rprint(Panel(f"[bold]Результат:[/bold] {msg}\nУ промпт (Top-3) цільова матриця {in_prompt}.", border_style="cyan"))


# ──────────────────────────────────────────────
# ЕКСПЕРИМЕНТ 4: Citation & Refusal Gate
# ──────────────────────────────────────────────


def _unsupported_numbers(query: str, chunks: list[Chunk]) -> list[str]:
    """Числа із запиту, яких немає в жодному з знайдених фрагментів."""
    import re

    def numbers(text: str) -> set[str]:
        # «100 000» -> «100000», щоб групи розрядів не рахувались окремими числами
        return set(re.findall(r"\d+", re.sub(r"(?<=\d)[ \u00a0](?=\d{3}\b)", "", text)))

    evidence = " ".join(c.text for c in chunks)
    return sorted(numbers(query) - numbers(evidence))


def run_experiment_4_refusal_gate(corpus: dict[str, str]) -> None:
    rprint("\n" + "=" * 75)
    rprint("[bold cyan]ЕКСПЕРИМЕНТ 4 · Citation & Refusal Gate[/bold cyan]")
    rprint("=" * 75)
    rprint("[dim]Правило гейту: відповідь дозволена, лише якщо кожне число із запиту підтверджене "
           "знайденими фрагментами політик; інакше — відмова з переліком того, чого немає.[/dim]\n")

    all_chunks: list[Chunk] = []
    for doc_id, text in corpus.items():
        all_chunks.extend(semantic_chunk_document(doc_id, text, max_tokens=300))
    pipeline = HybridSearchPipeline(use_api=False)
    pipeline.index_chunks(all_chunks)

    queries = [
        "відстрочка платежів на 18 місяців без відсотків зміна роботи",
        "ФОП без застави понад 100 000 грн строк реєстрації 12 місяців",
    ]
    table = Table(title="Рішення гейту на реальній видачі (Top-3)", show_lines=True)
    table.add_column("Запит")
    table.add_column("Знайдені джерела (Top-3)")
    table.add_column("Числа без підтвердження")
    table.add_column("Рішення гейту")
    for q in queries:
        res = pipeline.search_hybrid(q, top_k=3)
        chunks = [c for c, _ in res]
        missing = _unsupported_numbers(q, chunks)
        sources = "\n".join(f"{c.doc_id} · {c.section[:28]}" for c in chunks) or "—"
        if missing:
            decision = f"[bold red]ВІДМОВА[/bold red]: у знайдених нормах немає {', '.join(missing)}"
        else:
            decision = "[bold green]ДОЗВОЛЕНО[/bold green]: відповідь має цитуватись із джерел вище"
        table.add_row(q, sources, ", ".join(missing) or "—", decision)
    console.print(table)
    rprint(Panel("[dim]Гейт тут — проста детермінована перевірка чисел; у промисловому варіанті вона "
                 "доповнюється перевіркою цитат і порогом релевантності. Відповіді LLM у цьому "
                 "експерименті не генеруються.[/dim]", border_style="cyan"))


def main() -> None:
    parser = argparse.ArgumentParser(description="W2 RAG Optimization Ladder Lab")
    parser.add_argument("--exp", type=int, choices=[1, 2, 3, 4], help="Запустити конкретний експеримент (1-4)")
    args = parser.parse_args()

    rprint(
        Panel.fit(
            "[bold white]Loan Copilot · Webinar 2 Practical Lab[/bold white]\n"
            "[cyan]RAG Optimization Ladder · чанки, гібридний пошук, reranker та Citation Gate[/cyan]",
            border_style="cyan",
        )
    )

    corpus = load_corpus()
    if not corpus:
        rprint("[red]Помилка: корпус регламентів порожній![/red]")
        sys.exit(1)

    runners = {
        1: run_experiment_1_chunking,
        2: run_experiment_2_retrieval,
        3: run_experiment_3_reranker,
        4: run_experiment_4_refusal_gate,
    }
    for n in ([args.exp] if args.exp else [1, 2, 3, 4]):
        runners[n](corpus)
        if not args.exp:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
