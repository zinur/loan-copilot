"""Domain schemas (Pydantic) · shared across all modules.

Контракти I/O між agent'ами. Жоден agent не повертає `dict` — завжди
валідований Pydantic model. Без цього multi-agent систему неможливо
протестувати.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, EmailStr


# ────────────────────────────── core domain ──────────────────────────────


class LoanPurpose(str, Enum):
    consumer = "consumer"
    auto = "auto"
    refinance = "refinance"


class Applicant(BaseModel):
    full_name: str
    tax_id: str = Field(min_length=10, max_length=10, description="UA tax ID")
    phone: str
    email: EmailStr | None = None
    monthly_income: Decimal = Field(ge=0)


class LoanRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    term_months: int = Field(ge=3, le=84)
    purpose: LoanPurpose


class Application(BaseModel):
    """Повна заявка, що циркулює між agent'ами."""

    application_id: str
    applicant: Applicant
    request: LoanRequest
    kyc_passed: bool | None = None
    credit_score: int | None = Field(default=None, ge=300, le=850)
    decision: Literal["approve", "decline", "manual_review"] | None = None
    rate_apr: Decimal | None = None


# ────────────────────────────── agent I/O ──────────────────────────────


class AgentMessage(BaseModel):
    """Один turn у multi-agent діалозі.

    `from_agent` + `to_agent` формують audit log — хто що кому сказав.
    """

    from_agent: str
    to_agent: str
    content: str
    tool_calls: list[dict] = Field(default_factory=list)


class AgentResponse(BaseModel):
    """Відповідь agent'а на запит."""

    agent: str
    content: str
    next_action: Literal["handoff", "ask_user", "done", "escalate"] = "done"
    handoff_to: str | None = None
    data: dict = Field(default_factory=dict)
