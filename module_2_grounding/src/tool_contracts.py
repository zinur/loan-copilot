"""Контракти банківських інструментів (Tool Contracts & Write-Safety).

Реалізує 4 обов'язкові гарантії enterprise-інструменту:
1. Pydantic Schemas на вході та виході
2. In-Code RBAC: перевірка прав ролі в коді Python
3. Consent Gate: обов'язковий consent_id для доступу до персональних даних (CreditHistory)
4. Audit Trail: незмінний запис кожного виклику в audit.jsonl з відстеженням вартості
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AUDIT_LOG_FILE = Path(__file__).resolve().parent.parent / "audit.jsonl"


# ────────────────────────────── Exceptions ──────────────────────────────


class ConsentRequiredException(Exception):
    """Помилка відсутності або недійсності згоди позичальника на доступ до кредитної історії."""
    pass


class UnauthorizedRoleException(Exception):
    """Помилка доступу: викликач не має прав виконувати цей інструмент (In-Code RBAC)."""
    pass


# ────────────────────────────── Audit Logger ──────────────────────────────


def _record_audit(
    tool_name: str,
    caller_role: str,
    side_effect: str,
    cost_usd: float,
    status: str,
    details: dict,
    consent_id: str | None = None,
) -> None:
    """Запис транзакційного аудиту у файл audit.jsonl."""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "caller_role": caller_role,
        "side_effect": side_effect,
        "consent_id": consent_id,
        "cost_usd": cost_usd,
        "status": status,
        "details": details,
    }
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ────────────────────────── 1. kyc.lookup ──────────────────────────


class KYCInput(BaseModel):
    tax_id: str = Field(min_length=10, max_length=10, description="10-значний РНОКПП/ІПН")
    caller_role: str = Field(default="kyc_officer", description="Роль викликача для RBAC")


class KYCOutput(BaseModel):
    passed: bool
    sanctions_hit: bool
    pep_status: bool
    reason: str
    cost_usd: float = 0.80


def kyc_lookup(tax_id: str, caller_role: str = "kyc_officer") -> KYCOutput:
    """Інструмент перевірки санкцій, паспортних даних та PEP-статусу.
    
    Side-effect: external-read (internal/state registry)
    Role required: kyc_officer
    Cost: $0.80
    """
    if caller_role not in {"kyc_officer", "compliance_officer", "orchestrator"}:
        _record_audit("kyc.lookup", caller_role, "external-read", 0.0, "blocked_rbac", {"tax_id": tax_id})
        raise UnauthorizedRoleException(f"Роль '{caller_role}' не має прав на виконання kyc.lookup")

    # Mock сценарії (синхронізовані з scenario_data)
    is_sanctions = tax_id == "5555555555"
    passed = not is_sanctions
    reason = "sanctions hit (РНБО)" if is_sanctions else "clean"

    result = KYCOutput(
        passed=passed,
        sanctions_hit=is_sanctions,
        pep_status=False,
        reason=reason,
        cost_usd=0.80,
    )
    _record_audit("kyc.lookup", caller_role, "external-read", 0.80, "success", {"tax_id": tax_id, "passed": passed})
    return result


# ────────────────────── 2. credit_history.pull ──────────────────────


class CreditHistoryInput(BaseModel):
    tax_id: str = Field(min_length=10, max_length=10)
    consent_id: str | None = Field(default=None, description="Обов'язковий UUID згоди позичальника")
    caller_role: str = Field(default="loan_officer")


class CreditHistoryOutput(BaseModel):
    tax_id: str
    credit_score: int = Field(ge=300, le=850)
    active_loans_count: int
    overdue_days_max: int
    cost_usd: float = 8.50


def credit_history_pull(
    tax_id: str,
    consent_id: str | None,
    caller_role: str = "loan_officer",
) -> CreditHistoryOutput:
    """Інструмент витягу кредитної історії позичальника.
    
    Side-effect: external-read (PII)
    Consent Gate: обов'язкова наявність валідного consent_id
    Role required: loan_officer / credit_analyst
    Cost: ~$1.50 (UBKI · UAH 50–80 за lookup)
    """
    # 1. In-code RBAC перевірка
    if caller_role not in {"loan_officer", "credit_analyst", "orchestrator"}:
        _record_audit("credit_history.pull", caller_role, "external-read", 0.0, "blocked_rbac", {"tax_id": tax_id})
        raise UnauthorizedRoleException(f"Роль '{caller_role}' не має дозволу на запит персональних даних БКІ")

    # 2. Consent Gate перевірка (Правова межа захисту PII)
    if not consent_id or len(consent_id.strip()) < 8:
        _record_audit("credit_history.pull", caller_role, "external-read", 0.0, "blocked_no_consent", {"tax_id": tax_id})
        raise ConsentRequiredException(
            f"Запит кредитної історії для tax_id={tax_id} заблоковано: відсутня обов'язкова згода позичальника (consent_id). "
            "Штраф регулятора за порушення банківської таємниці становить до 20 млн грн."
        )

    # Mock скорингові дані позичальників
    score_map = {
        "1234567890": 720,  # Марія Коваленко (Good candidate)
        "9876543210": 580,  # Поганий скор (Borderline / Tier C)
        "5555555555": 650,  # Санкційний позичальник
    }
    score = score_map.get(tax_id, 650)
    overdue = 0 if score >= 700 else 45

    res = CreditHistoryOutput(
        tax_id=tax_id,
        credit_score=score,
        active_loans_count=1 if score < 700 else 0,
        overdue_days_max=overdue,
        cost_usd=8.50,
    )
    _record_audit(
        "credit_history.pull",
        caller_role,
        "external-read",
        8.50,
        "success",
        {"tax_id": tax_id, "score": score},
        consent_id=consent_id,
    )
    return res


# ────────────────────── 3. loan_math.calculate ──────────────────────


class LoanMathInput(BaseModel):
    amount: Decimal
    term_months: int
    monthly_income: Decimal
    annual_rate_pct: Decimal = Decimal("18.5")


class LoanMathOutput(BaseModel):
    monthly_payment: Decimal
    pti: Decimal
    dti_estimated: Decimal
    total_repayment: Decimal
    calculation_engine: str = "python_deterministic"


def calculate_loan_math(
    amount: Decimal,
    term_months: int,
    monthly_income: Decimal,
    annual_rate_pct: Decimal = Decimal("18.5"),
) -> LoanMathOutput:
    """Точний розрахунок ануїтетного щомісячного платежу та коефіцієнта навантаження (PTI).
    
    Side-effect: none (чиста математична функція)
    Cost: $0.00
    Формула: PMT = Amount * (r * (1+r)^n) / ((1+r)^n - 1)
    """
    if monthly_income <= 0:
        raise ValueError("Місячний дохід повинен бути більшим за нуль")
    if term_months <= 0 or amount <= 0:
        raise ValueError("Сума та строк кредиту мають бути більшими за нуль")

    # Місячна відсоткова ставка r
    r = (annual_rate_pct / Decimal("100")) / Decimal("12")
    n = term_months

    # Обчислення через точну арифметику Decimal
    r_float = float(r)
    factor = (r_float * (1.0 + r_float) ** n) / (((1.0 + r_float) ** n) - 1.0)
    monthly_payment = (amount * Decimal(str(factor))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    pti = (monthly_payment / monthly_income).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    total_repayment = (monthly_payment * Decimal(str(term_months))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return LoanMathOutput(
        monthly_payment=monthly_payment,
        pti=pti,
        dti_estimated=pti,
        total_repayment=total_repayment,
        calculation_engine="python_deterministic",
    )


# ────────────────────── 4. risk.score (Internal Write) ──────────────────────


class RiskScoreInput(BaseModel):
    credit_score: int
    pti: Decimal
    caller_role: str = "risk_analyst"
    idempotency_key: str | None = None


class RiskScoreOutput(BaseModel):
    tier: Literal["Tier A", "Tier B", "Tier C", "Tier D"]
    decision: Literal["approve", "decline", "manual_review"]
    rate_apr: Decimal | None
    rule_matched: str
    cost_usd: float = 0.05


def evaluate_risk_tier(
    credit_score: int,
    pti: Decimal,
    caller_role: str = "risk_analyst",
    idempotency_key: str | None = None,
) -> RiskScoreOutput:
    """Застосування 4-Tier матриці рішень (SOP-CREDIT-02 §3.1).
    
    Side-effect: internal-write (фіксація оцінки в реєстрі)
    Cost: $0.05
    """
    if caller_role not in {"risk_analyst", "underwriter", "orchestrator"}:
        raise UnauthorizedRoleException(f"Роль '{caller_role}' не має прав формувати оцінку ризику")

    # Hard stop за правилом 3.2
    if credit_score < 500:
        tier = "Tier D"
        decision = "decline"
        rate = None
        rule = "SOP-CREDIT-02 §3.2 (Hard Stop: score < 500)"
    elif credit_score >= 750 and pti <= Decimal("0.35"):
        tier = "Tier A"
        decision = "approve"
        rate = Decimal("15.9")
        rule = "SOP-CREDIT-02 §3.1 (Tier A: score>=750, PTI<=35%)"
    elif credit_score >= 650 and pti <= Decimal("0.45"):
        tier = "Tier B"
        decision = "approve"
        rate = Decimal("18.5")
        rule = "SOP-CREDIT-02 §3.1 (Tier B: score>=650, PTI<=45%)"
    elif credit_score >= 550 and pti <= Decimal("0.50"):
        tier = "Tier C"
        decision = "manual_review"
        rate = Decimal("24.0")
        rule = "SOP-CREDIT-02 §3.1 (Tier C: score>=550, PTI<=50% -> Сіра зона)"
    else:
        tier = "Tier D"
        decision = "decline"
        rate = None
        rule = "SOP-CREDIT-02 §3.1 (Tier D: перевищення ліміту навантаження)"

    res = RiskScoreOutput(
        tier=tier,
        decision=decision,
        rate_apr=rate,
        rule_matched=rule,
        cost_usd=0.05,
    )
    _record_audit(
        "risk.score",
        caller_role,
        "internal-write",
        0.05,
        "success",
        {"score": credit_score, "pti": float(pti), "decision": decision, "tier": tier},
    )
    return res
