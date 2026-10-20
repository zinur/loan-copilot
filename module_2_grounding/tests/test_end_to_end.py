"""End-to-End тести для W2 Grounded Orchestrator, Tool Contracts та Consent Gate."""

from __future__ import annotations

from decimal import Decimal
import pytest

from common.scenario_data import SAMPLE_APPLICATION
from module_2_grounding.src.eligibility_agent import EligibilityAgent
from module_2_grounding.src.orchestrator_v2 import FlowStateV2, OrchestratorV2
from module_2_grounding.src.tool_contracts import (
    ConsentRequiredException,
    UnauthorizedRoleException,
    calculate_loan_math,
    credit_history_pull,
    evaluate_risk_tier,
    kyc_lookup,
)


def test_happy_path_orchestrator():
    """Тест 1: Успішне проходження повного конвеєра з RAG та інструментами."""
    orch = OrchestratorV2(use_api=False)
    state = FlowStateV2(
        application=SAMPLE_APPLICATION.model_copy(deep=True),
        applicant_age=28,
        is_fop=False,
        consent_id="valid-consent-uuid-mariia-2026",
    )
    res = orch.run(state, verbose=False)

    assert res.decision == "approve"
    assert res.tier == "Tier B"
    assert res.rate_apr == Decimal("18.5")
    assert res.credit_score == 720
    assert res.monthly_payment is not None
    assert res.monthly_payment > Decimal("7000")
    assert res.pti is not None
    assert res.pti < Decimal("0.30")
    assert any("POL-LOAN-01" in c for c in res.citations)
    assert any("SOP-CREDIT-02" in c for c in res.citations)


def test_consent_gate_enforcement():
    """Тест 2: Consent Gate фізично блокує запит кредитної історії без згоди."""
    orch = OrchestratorV2(use_api=False)
    state = FlowStateV2(
        application=SAMPLE_APPLICATION.model_copy(deep=True),
        applicant_age=28,
        is_fop=False,
        consent_id=None,  # Без згоди!
    )
    res = orch.run(state, verbose=False)

    assert res.decision == "decline"
    assert res.credit_score is None  # Жодні дані БКІ не були отримані

    # Прямий виклик тулу без згоди має викликати виняток ConsentRequiredException
    with pytest.raises(ConsentRequiredException):
        credit_history_pull(tax_id="1234567890", consent_id=None, caller_role="loan_officer")


def test_in_code_rbac_security():
    """Тест 3: In-Code RBAC блокує спробу виклику інструментів неавторизованою роллю."""
    # Роль 'customer_support' не має права робити кредитний pull
    with pytest.raises(UnauthorizedRoleException):
        credit_history_pull(
            tax_id="1234567890",
            consent_id="valid-consent-12345",
            caller_role="customer_support",
        )

    # Роль 'marketing_analyst' не має права робити KYC перевірку
    with pytest.raises(UnauthorizedRoleException):
        kyc_lookup(tax_id="1234567890", caller_role="marketing_analyst")


def test_fop_unsecured_exemption_rag():
    """Тест 4: RAG виявляє виняток у §4.2.1 для ФОП понад 100k без застави."""
    agent = EligibilityAgent(use_api=False)
    fop_app = SAMPLE_APPLICATION.model_copy(deep=True)
    fop_app.request.amount = Decimal("150000")

    # ФОП зі стажем 7 місяців (менше 12 місяців)
    res = agent.evaluate(
        app=fop_app,
        applicant_age=30,
        is_fop=True,
        fop_registration_months=7,
    )
    assert not res.is_eligible
    assert res.status == "ineligible"
    assert "POL-LOAN-01 §4.2.1" in res.citations


def test_deterministic_loan_math():
    """Тест 5: Перевірка точності фінансової математики без LLM."""
    math_res = calculate_loan_math(
        amount=Decimal("150000"),
        term_months=24,
        monthly_income=Decimal("35000"),
        annual_rate_pct=Decimal("18.5"),
    )
    # Перевірка щомісячного ануїтету
    assert math_res.monthly_payment == Decimal("7524.90")
    # Перевірка PTI (7524.90 / 35000 = 0.215)
    assert math_res.pti == Decimal("0.215")
    assert math_res.total_repayment == Decimal("180597.60")
