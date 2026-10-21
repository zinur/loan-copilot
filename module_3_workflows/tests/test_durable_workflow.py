"""Unit-тести для перевірки надійності, HITL, Saga-відкатів та ідемпотентності."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from module_3_workflows.src.workflow_engine import (
    LoanWorkflowState,
    build_loan_workflow,
)


def _get_base_state(app_id: str = "LC-TEST-001") -> LoanWorkflowState:
    return {
        "application_id": app_id,
        "applicant_name": "Тестовий Позичальник",
        "tax_id": "1234567890",
        "amount": 100000.0,
        "term_months": 24,
        "monthly_income": 30000.0,
        "consent_id": "consent-uuid-valid-1234",
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


def test_happy_path_workflow():
    """Тест 1: Успішне проходження повного конвеєра до фінальної виплати."""
    app = build_loan_workflow(checkpointer=MemorySaver())
    state = _get_base_state("LC-HAPPY-TEST")
    cfg = {"configurable": {"thread_id": "thread-test-happy"}}

    res = app.invoke(state, config=cfg)

    assert res["status"] == "disbursed"
    assert res["eligibility_passed"] is True
    assert res["kyc_passed"] is True
    assert res["credit_score"] == 720
    assert res["limit_reserved"] is True
    assert res["signed"] is True
    assert res["contract_id"] is not None


def test_hitl_pause_and_resume():
    """Тест 2: HITL-зупинка в сірій зоні Tier C та успішне відновлення за thread_id."""
    checkpointer = MemorySaver()
    app = build_loan_workflow(checkpointer=checkpointer)

    state = _get_base_state("LC-HITL-TEST")
    state["tax_id"] = "9876543210"  # скор 580 -> сіра зона Tier C!
    cfg = {"configurable": {"thread_id": "thread-test-hitl"}}

    # Первинний запуск повинен зупинитися на HITL-вузлі
    res_paused = app.invoke(state, config=cfg)
    assert res_paused["status"] == "awaiting_hitl"
    assert res_paused["hitl_required"] is True
    assert res_paused["current_node"] == "hitl_committee"

    # Людина підтверджує через інтерфейс перевизначення (Human Override)
    res_resumed = app.invoke(
        {"human_decision": "approve", "human_comment": "Погоджено під депозит"},
        config=cfg,
    )
    assert res_resumed["status"] == "disbursed"
    assert res_resumed["hitl_required"] is False
    assert "human_override_approved" in res_resumed["audit_trail"]


def test_saga_compensating_action():
    """Тест 3: Saga Pattern — відкат ліміту та анулювання договору при скасуванні."""
    app = build_loan_workflow(checkpointer=MemorySaver())
    state = _get_base_state("LC-SAGA-TEST")
    state["client_cancelled"] = True  # Клієнт відмовився після генерації договору
    cfg = {"configurable": {"thread_id": "thread-test-saga"}}

    res = app.invoke(state, config=cfg)

    assert res["status"] == "cancelled"
    assert res["limit_reserved"] is False  # Ліміт обов'язково має бути звільнено!
    assert res["compensation_executed"] is True
    assert "limit_released_rollback" in res["audit_trail"]
    assert "contract_voided" in res["audit_trail"]


def test_webhook_idempotency_barrier():
    """Тест 4: Ідемпотентний бар'єр запобігає повторній обробці однакових подій підпису."""
    from module_3_workflows.src.workflow_engine import sign_awaiting_node

    state = _get_base_state("LC-IDEM-TEST")
    # Перший виклик
    out1 = sign_awaiting_node(state)
    assert out1.get("signed") is True
    first_key = out1["processed_events"][0]

    # Другий виклик із тим самим ключем (дублікат від Дія.Підпис)
    state["processed_events"] = [first_key]
    out2 = sign_awaiting_node(state)
    # Повторний виклик не повинен змінювати стан або повторно підписувати
    assert "signed" not in out2
    assert out2["current_node"] == "sign_awaiting"
