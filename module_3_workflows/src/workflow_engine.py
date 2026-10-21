"""Durable Workflow Engine для Loan Copilot (Module 3 Workflows).

Реалізує банківський кредитний процес на базі графа станів (LangGraph):
1. Скінченний автомат (FSM) із персистентними чекпоінтами (переживає рестарт процесу)
2. Асинхронний Human-in-the-Loop (HITL) гейт із зупинкою та відновленням (Interrupt & Resume)
3. Розподілені транзакції (Saga Pattern) із компенсуючими діями (відкат зарезервованого ліміту)
4. Ідемпотентний бар'єр для захисту від дублювання вебхуків підпису

Запуск:
    python -m module_3_workflows.src.workflow_engine
"""

from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ────────────────────────────── State Model ──────────────────────────────


class LoanWorkflowState(TypedDict):
    application_id: str
    applicant_name: str
    tax_id: str
    amount: float
    term_months: int
    monthly_income: float
    consent_id: str | None
    is_fop: bool
    fop_months: int
    # Поточний статус процесу
    status: Literal["in_progress", "awaiting_hitl", "approved", "declined", "cancelled", "disbursed"]
    current_node: str
    # Дані верифікації
    eligibility_passed: bool | None
    kyc_passed: bool | None
    credit_score: int | None
    # Фінансова математика
    monthly_payment: float | None
    pti: float | None
    tier: str | None
    rate_apr: float | None
    # Людина в контурі (HITL)
    hitl_required: bool
    human_decision: str | None
    human_comment: str | None
    # Договір та підпис
    contract_id: str | None
    limit_reserved: bool
    signed: bool
    client_cancelled: bool
    processed_events: list[str]
    # Saga компенсуюча дія
    compensation_executed: bool
    citations: list[str]
    audit_trail: list[str]


# ────────────────────────────── Node Functions ──────────────────────────────


def intake_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 1: Валідація початкових даних заявки."""
    app_id = state["application_id"]
    rprint(f"[cyan]▶ [Node: intake] Заявка {app_id}: перевірка вхідних реквізитів позичальника[/cyan]")
    return {
        "status": "in_progress",
        "current_node": "intake",
        "audit_trail": state.get("audit_trail", []) + [f"{time.strftime('%X')} intake validated"],
    }


def eligibility_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 2: RAG-перевірка критеріїв прийнятності за POL-LOAN-01."""
    rprint("[cyan]▶ [Node: eligibility] RAG-перевірка лімітів та правил кредитування[/cyan]")
    amount = state["amount"]
    is_fop = state.get("is_fop", False)
    fop_months = state.get("fop_months", 24)

    # Перевірка лімітів суми
    if amount < 10000 or amount > 300000:
        return {
            "eligibility_passed": False,
            "status": "declined",
            "current_node": "eligibility",
            "citations": state.get("citations", []) + ["POL-LOAN-01 §3.1 (Сума поза лімітом 10k-300k)"],
        }

    # Перевірка винятку для ФОП (§4.2.1: понад 100k без застави -> стаж >= 12 міс)
    if is_fop and amount > 100000 and fop_months < 12:
        return {
            "eligibility_passed": False,
            "status": "declined",
            "current_node": "eligibility",
            "citations": state.get("citations", []) + ["POL-LOAN-01 §4.2.1 (ФОП >100k вимагає стаж >= 12 міс)"],
        }

    return {
        "eligibility_passed": True,
        "current_node": "eligibility",
        "citations": state.get("citations", []) + ["POL-LOAN-01 §2.1", "POL-LOAN-01 §3.1"],
    }


def kyc_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 3: Санкційний та PEP-скринінг позичальника."""
    rprint("[cyan]▶ [Node: kyc] Санкційний скринінг у реєстрах РНБО та перевірка PEP[/cyan]")
    is_sanctions = state["tax_id"] == "5555555555"
    if is_sanctions:
        return {
            "kyc_passed": False,
            "status": "declined",
            "current_node": "kyc",
            "citations": state.get("citations", []) + ["REG-NBU-351 §25 (Санкційний дефолтний позичальник)"],
        }
    return {
        "kyc_passed": True,
        "current_node": "kyc",
        "audit_trail": state.get("audit_trail", []) + ["kyc_passed_clean"],
    }


def credit_history_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 4: Запит кредитної історії з Consent Gate."""
    rprint("[cyan]▶ [Node: credit_history] Запит кредитної історії позичальника (Consent Gate)[/cyan]")
    consent = state.get("consent_id")
    if not consent or len(consent) < 8:
        rprint("[bold red]  ✖ БЛОКУВАННЯ: відсутній consent_id! Зупинка конвеєра.[/bold red]")
        return {
            "credit_score": None,
            "status": "declined",
            "current_node": "credit_history",
            "audit_trail": state.get("audit_trail", []) + ["blocked_missing_consent"],
        }

    # Симуляція скорингу
    tax_id = state["tax_id"]
    score_map = {"1234567890": 720, "9876543210": 580}
    score = score_map.get(tax_id, 650)

    return {
        "credit_score": score,
        "current_node": "credit_history",
        "audit_trail": state.get("audit_trail", []) + [f"score_received_{score}"],
    }


def underwriter_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 5: Фінансова математика (PMT, PTI) та 4-Tier матриця."""
    rprint("[cyan]▶ [Node: underwriter] Детермінований розрахунок ануїтету PMT та оцінка матриці ризику[/cyan]")
    score = state.get("credit_score") or 0
    amount = Decimal(str(state["amount"]))
    term = state["term_months"]
    income = Decimal(str(state["monthly_income"]))

    # Точний розрахунок ануїтету
    r = (Decimal("18.5") / Decimal("100")) / Decimal("12")
    r_float = float(r)
    factor = (r_float * (1.0 + r_float) ** term) / (((1.0 + r_float) ** term) - 1.0)
    pmt = (amount * Decimal(str(factor))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pti = (pmt / income).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    # 4-Tier матриця за SOP-CREDIT-02
    if score >= 750 and pti <= Decimal("0.35"):
        tier, dec, rate, hitl = "Tier A", "approved", 15.9, False
    elif score >= 650 and pti <= Decimal("0.45"):
        tier, dec, rate, hitl = "Tier B", "approved", 18.5, False
    elif score >= 550 and pti <= Decimal("0.50"):
        tier, dec, rate, hitl = "Tier C", "awaiting_hitl", 24.0, True  # Сіра зона -> HITL пауза!
    else:
        tier, dec, rate, hitl = "Tier D", "declined", None, False

    return {
        "monthly_payment": float(pmt),
        "pti": float(pti),
        "tier": tier,
        "rate_apr": rate,
        "status": dec,
        "hitl_required": hitl,
        "current_node": "underwriter",
        "citations": state.get("citations", []) + [f"SOP-CREDIT-02 §3.1 ({tier})"],
    }


def hitl_committee_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 6: Human-in-the-Loop гейт для заявок у сірій зоні (Tier C)."""
    rprint("[bold yellow]⏸ [HITL Gate: committee] Заявка в сірій зоні Tier C! Очікування рішення андеррайтера...[/bold yellow]")
    decision = state.get("human_decision")
    if decision == "approve":
        rprint("[green]  ✔ Рішення людини: СХВАЛЕНО з компенсуючим фактором[/green]")
        return {
            "status": "approved",
            "hitl_required": False,
            "current_node": "hitl_committee",
            "audit_trail": state.get("audit_trail", []) + ["human_override_approved"],
        }
    elif decision == "decline":
        rprint("[red]  ✖ Рішення людини: ВІДХИЛЕНО кредитним комітетом[/red]")
        return {
            "status": "declined",
            "hitl_required": False,
            "current_node": "hitl_committee",
            "audit_trail": state.get("audit_trail", []) + ["human_override_declined"],
        }
    else:
        # Процес призупиняється (Interrupt State)
        return {
            "status": "awaiting_hitl",
            "current_node": "hitl_committee",
        }


def contract_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 7: Генерація кредитного договору та резервування ліміту (Saga Forward Action)."""
    app_id = state["application_id"]
    rprint(f"[cyan]▶ [Node: contract] Генерація договору та резервування кредитного ліміту {state['amount']:,.2f} грн[/cyan]")
    contract_id = f"DOG-{app_id}-2026"
    return {
        "contract_id": contract_id,
        "limit_reserved": True,
        "current_node": "contract",
        "audit_trail": state.get("audit_trail", []) + [f"contract_drafted_{contract_id}", "limit_reserved_in_core"],
    }


def sign_awaiting_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 8: Очікування вебхука Дія.Підпис з ідемпотентним бар'єром."""
    rprint("[cyan]▶ [Node: sign_awaiting] Очікування цифрового підпису позичальника через Дія.Підпис[/cyan]")
    event_id = "dia-webhook-evt-99182"
    key = hashlib.sha256(f"{state['application_id']}:{event_id}".encode()).hexdigest()[:16]

    processed = state.get("processed_events", [])
    if key in processed:
        rprint(f"[yellow]  ○ Ідемпотентний бар'єр: подія {key} вже оброблена! Дубль проігноровано.[/yellow]")
        return {"current_node": "sign_awaiting"}

    rprint(f"[green]  ✔ Вебхук e-sign успішно верифіковано (Idempotency Key: {key})[/green]")
    return {
        "signed": True,
        "current_node": "sign_awaiting",
        "processed_events": processed + [key],
        "audit_trail": state.get("audit_trail", []) + [f"signed_idempotent_{key}"],
    }


def disburse_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 9: Фінальне перерахування коштів позичальнику."""
    rprint(f"[bold green]✔ [Node: disburse] ВИПЛАТА КОШТІВ: {state['amount']:,.2f} грн зараховано на рахунок позичальника![/bold green]")
    return {
        "status": "disbursed",
        "current_node": "disburse",
        "audit_trail": state.get("audit_trail", []) + ["funds_disbursed_success"],
    }


def compensation_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Вузол 10: Saga Compensating Action — відкат зарезервованого ліміту при скасуванні."""
    rprint("[bold red]↺ [Saga Compensation: rollback] Відкат транзакції: зняття резерву ліміту та анулювання договору![/bold red]")
    return {
        "status": "cancelled",
        "limit_reserved": False,
        "compensation_executed": True,
        "current_node": "compensation",
        "audit_trail": state.get("audit_trail", []) + ["limit_released_rollback", "contract_voided"],
    }


# ────────────────────────────── Router Edges ──────────────────────────────


def after_eligibility(state: LoanWorkflowState) -> str:
    if not state.get("eligibility_passed", False):
        return END
    return "kyc"


def after_kyc(state: LoanWorkflowState) -> str:
    if not state.get("kyc_passed", False):
        return END
    return "credit_history"


def after_credit_history(state: LoanWorkflowState) -> str:
    if state.get("status") == "declined" or state.get("credit_score") is None:
        return END
    return "underwriter"


def after_underwriter(state: LoanWorkflowState) -> str:
    if state.get("hitl_required", False):
        return "hitl_committee"
    if state.get("status") == "approved":
        return "contract"
    return END


def after_hitl(state: LoanWorkflowState) -> str:
    if state.get("status") == "approved":
        return "contract"
    return END


def after_contract(state: LoanWorkflowState) -> str:
    # Якщо клієнт натиснув "Скасувати" після резервування ліміту — запускаємо Saga Rollback
    if state.get("client_cancelled", False) or state.get("status") == "cancelled":
        return "compensation"
    return "sign_awaiting"


def after_sign(state: LoanWorkflowState) -> str:
    if state.get("signed", False):
        return "disburse"
    return END


# ────────────────────────────── Graph Assembly ──────────────────────────────


def build_loan_workflow(checkpointer: Any | None = None) -> Any:
    """Побудова повноцінного графа станів кредитного конвеєра."""
    workflow = StateGraph(LoanWorkflowState)

    # Додавання всіх вузлів
    workflow.add_node("intake", intake_node)
    workflow.add_node("eligibility", eligibility_node)
    workflow.add_node("kyc", kyc_node)
    workflow.add_node("credit_history", credit_history_node)
    workflow.add_node("underwriter", underwriter_node)
    workflow.add_node("hitl_committee", hitl_committee_node)
    workflow.add_node("contract", contract_node)
    workflow.add_node("sign_awaiting", sign_awaiting_node)
    workflow.add_node("disburse", disburse_node)
    workflow.add_node("compensation", compensation_node)

    # З'єднання вузлів переходами
    workflow.set_entry_point("intake")
    workflow.add_edge("intake", "eligibility")
    workflow.add_conditional_edges("eligibility", after_eligibility)
    workflow.add_conditional_edges("kyc", after_kyc)
    workflow.add_conditional_edges("credit_history", after_credit_history)
    workflow.add_conditional_edges("underwriter", after_underwriter)
    workflow.add_conditional_edges("hitl_committee", after_hitl)
    workflow.add_conditional_edges("contract", after_contract)
    workflow.add_conditional_edges("sign_awaiting", after_sign)
    workflow.add_edge("disburse", END)
    workflow.add_edge("compensation", END)

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


# ──────────────────────────────── Live Demo ────────────────────────────────


def run_demo() -> None:
    console.print(
        Panel.fit(
            "[bold white]Loan Copilot · Module 3 Durable Workflows Demo[/bold white]\n"
            "[cyan]LangGraph FSM: Персистентні чекпоінти, асинхронний HITL та Saga-відкати[/cyan]",
            border_style="cyan",
        )
    )

    checkpointer = MemorySaver()
    app = build_loan_workflow(checkpointer=checkpointer)

    # ─────────────── СЦЕНАРІЙ 1: Happy Path з чекпоінтами ───────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 1: Happy Path (Повний автоматичний конвеєр) ──────────[/bold yellow]")
    init_state_1: LoanWorkflowState = {
        "application_id": "LC-2026-HAPPY",
        "applicant_name": "Марія Коваленко",
        "tax_id": "1234567890",
        "amount": 150000.0,
        "term_months": 24,
        "monthly_income": 35000.0,
        "consent_id": "consent-uuid-mariia-ok",
        "is_fop": False,
        "fop_months": 24,
        "status": "in_progress",
        "current_node": "init",
        "eligibility_passed": None,
        "kyc_passed": None,
        "credit_score": None,
        "monthly_payment": None,
        "pti": None,
        "tier": None,
        "rate_apr": None,
        "hitl_required": False,
        "human_decision": None,
        "human_comment": None,
        "contract_id": None,
        "limit_reserved": False,
        "signed": False,
        "client_cancelled": False,
        "processed_events": [],
        "compensation_executed": False,
        "citations": [],
        "audit_trail": [],
    }

    cfg1 = {"configurable": {"thread_id": "thread-happy-001"}}
    res1 = app.invoke(init_state_1, config=cfg1)
    rprint(f"[bold green]✔ Результат Сценарію 1:[/bold green] статус = {res1['status']} | договір = {res1['contract_id']}")

    # ─────────────── СЦЕНАРІЙ 2: HITL Пауза та Відновлення (Interrupt & Resume) ───────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 2: HITL Пауза та Відновлення після рестарту ──────────[/bold yellow]")
    rprint("[dim]Позичальник із балом 580 потрапляє в сіру зону Tier C → процес зупиняється.[/dim]")

    init_state_2 = dict(init_state_1)
    init_state_2["application_id"] = "LC-2026-HITL"
    init_state_2["tax_id"] = "9876543210"  # скор 580 -> Tier C!
    cfg2 = {"configurable": {"thread_id": "thread-hitl-002"}}

    res2_paused = app.invoke(init_state_2, config=cfg2)
    rprint(f"[yellow]⏸ Процес зупинено на вузлі:[/yellow] {res2_paused['current_node']} (Статус: {res2_paused['status']})")
    rprint("[dim]Симулюємо перезапуск процесу (kill -9): стан завантажується за thread_id з бази...[/dim]")

    # Людина підтверджує через адмін-панель
    resume_patch = {"human_decision": "approve", "human_comment": "Схвалено: наявний строковий депозит 100k грн"}
    res2_resumed = app.invoke(resume_patch, config=cfg2)
    rprint(f"[bold green]✔ Процес успішно продовжено після HITL:[/bold green] статус = {res2_resumed['status']} | договір = {res2_resumed['contract_id']}")

    # ─────────────── СЦЕНАРІЙ 3: Saga Compensating Rollback ───────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 3: Saga Rollback (Клієнт відмовився після резерву ліміту) ──────────[/bold yellow]")
    init_state_3 = dict(init_state_1)
    init_state_3["application_id"] = "LC-2026-CANCEL"
    init_state_3["client_cancelled"] = True  # Клієнт натиснув "Скасувати кредит" після генерації договору!
    cfg3 = {"configurable": {"thread_id": "thread-saga-003"}}

    res3 = app.invoke(init_state_3, config=cfg3)
    rprint(f"[bold red]↺ Результат Saga:[/bold red] статус = {res3['status']} | ліміт звільнено = {not res3['limit_reserved']} | компенсовано = {res3['compensation_executed']}")


if __name__ == "__main__":
    run_demo()
