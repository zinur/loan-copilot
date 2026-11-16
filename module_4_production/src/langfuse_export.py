"""Експорт результатів eval у Langfuse v2 (локальний docker): трейс на кейс + оцінки (scores).

Ключі лише з env: LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
Без ключів або без пакета `langfuse` — тихо пропускає (eval не залежить від Langfuse).
"""

from __future__ import annotations

import os


def export_results(results, run_name: str, answers: dict[str, str] | None = None,
                   dataset_name: str | None = None, cases: list[dict] | None = None) -> str | None:
    """Повертає URL трейсів або None, якщо експорт неможливий."""
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        print("Langfuse: пропущено (немає LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY).")
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        print("Langfuse: пропущено (pip install 'langfuse<3').")
        return None

    import importlib.metadata as md

    ver = md.version("langfuse")
    if int(ver.split(".")[0]) >= 3:
        print(f"Langfuse: встановлено SDK {ver}, а код і сервер — v2. Виконайте: pip install 'langfuse<3'")
        return None

    lf = Langfuse(public_key=pk, secret_key=sk, host=host)
    try:
        ok = lf.auth_check()
    except Exception as e:  # noqa: BLE001
        print(f"Langfuse: немає зв'язку з {host}: {e}")
        return None
    if not ok:
        print(f"Langfuse: ключі не прийняті сервером {host} (перевірте, що ключі з ТОГО Ж проєкту).")
        return None
    print(f"Langfuse: SDK {ver}, зв'язок з {host} OK, відправляю {len(results)} трейсів (session={run_name})")
    items = {}
    if dataset_name and cases:
        lf.create_dataset(name=dataset_name, description="Loan Copilot golden set (W4)")
        for c in cases:  # id = case_id → повторний запуск оновлює, а не дублює
            lf.create_dataset_item(
                dataset_name=dataset_name, id=c["id"], input=c["question"],
                expected_output={"decision": c["expected_verdict"], "citations": c.get("required_citations", [])},
                metadata={"category": c["category"]},
            )
        items = {i.id: i for i in lf.get_dataset(dataset_name).items}
        print(f"Langfuse: dataset '{dataset_name}' — {len(items)} елементів")

    for r in results:
        trace = lf.trace(
            name=f"golden/{r.case_id}",
            session_id=run_name,
            input=r.question,
            output=(answers or {}).get(r.case_id, ""),
            tags=["w4-eval", r.category],
            metadata={"details": r.details, "passed": r.passed},
        )
        scores = {
            "verdict_match": float(r.verdict_match),
            "faithfulness": r.faithfulness,
            "answer_relevance": r.answer_relevance,
            "context_recall": r.context_recall,
            "pii_leak": float(r.pii_leak_detected),
            "injection_succeeded": float(r.injection_succeeded),
            "passed": float(r.passed),
        }
        for name, value in scores.items():
            lf.score(trace_id=trace.id, name=name, value=value)
        if r.case_id in items:
            items[r.case_id].link(trace, run_name)  # запуск (run) у Datasets → порівняння між запусками
    lf.flush()
    print("Langfuse: flush виконано. В UI оберіть правильний проєкт і Tracing → Sessions.")
    return f"{host}/project"
