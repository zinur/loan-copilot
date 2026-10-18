"""Orchestrator · supervisor pattern · deterministic routing.

Містить 4 agent'и W1: Intake, KYC, Bureau, Underwriter.
ContractAgent + Compliance додаєте у вправі 05-exercise.

Запуск демо з моками:
    python -m module_1_agents.src.orchestrator

У цьому прикладі intake симулюється seed-даними (не REPL), щоб
побачити повний flow автоматично.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from common.scenario_data import MOCK_BUREAU_SCORES, MOCK_KYC_RESULTS, SAMPLE_APPLICATION
from common.schemas import AgentResponse, Application


# ────────────────────────────── routing ──────────────────────────────


def _intake_complete(state: Application) -> bool:
    """Усі обов'язкові поля заповнені."""
    a = state.applicant
    return bool(a.full_name and a.tax_id and a.phone and a.monthly_income)


def route(state: Application) -> str | None:
    """Deterministic routing · повертає ім'я наступного agent'а, або None."""
    if not _intake_complete(state):
        return "IntakeAgent"
    if state.kyc_passed is None:
        return "KYCAgent"
    if not state.kyc_passed:
        return None  # відмова — KYC fail
    if state.bureau_score is None:
        return "BureauAgent"
    if state.decision is None:
        return "UnderwriterAgent"
    return None  # final


# ──────────────────────────── agent stubs ────────────────────────────
#
# У W1 ми не підключаємо реальні bureau/KYC APIs — це робота W2.
# Тут мокнуті виклики для демонстрації flow.


def kyc_agent(state: Application, user_msg: str) -> AgentResponse:
    """KYC check — викликає мокнутий KYC API."""
    result = MOCK_KYC_RESULTS.get(
        state.applicant.tax_id, {"passed": False, "sanctions": False, "pep": False}
    )
    msg = (
        "KYC passed · identity confirmed"
        if result["passed"]
        else "KYC failed · " + ("sanctions hit" if result["sanctions"] else "data mismatch")
    )
    return AgentResponse(
        agent="KYCAgent",
        content=msg,
        next_action="handoff" if result["passed"] else "escalate",
        handoff_to="BureauAgent" if result["passed"] else None,
        data={"kyc_passed": result["passed"]},
    )


def bureau_agent(state: Application, user_msg: str) -> AgentResponse:
    """Bureau pull — мокнуте FICO-подібне число."""
    score = MOCK_BUREAU_SCORES.get(state.applicant.tax_id, 600)
    return AgentResponse(
        agent="BureauAgent",
        content=f"Bureau score отримано: {score}",
        next_action="handoff",
        handoff_to="UnderwriterAgent",
        data={"bureau_score": score},
    )


def underwriter_agent(state: Application, user_msg: str) -> AgentResponse:
    """Policy decision за simple rule-based thresholds.

    У W2 замінимо на LLM-powered reasoning з SOP grounding. Поки — чистий
    Python, щоб було видно, що це НЕ має бути LLM виклик, якщо правила
    формалізовані.
    """
    score = state.bureau_score or 0
    income = state.applicant.monthly_income
    amount = state.request.amount
    pti = (amount / Decimal(state.request.term_months)) / income if income else Decimal("99")

    if score >= 700 and pti < Decimal("0.5"):
        decision, rate = "approve", Decimal("18.5")
    elif score < 600 or pti >= Decimal("0.5"):
        decision, rate = "decline", None
    else:
        decision, rate = "manual_review", None

    text = f"Decision: {decision}" + (f", APR {rate}%" if rate else "")
    return AgentResponse(
        agent="UnderwriterAgent",
        content=text,
        next_action="done",
        data={"decision": decision, "rate_apr": rate},
    )


def intake_agent_mock(state: Application, user_msg: str) -> AgentResponse:
    """У demo seed'имо intake вже заповненим. Справжній REPL — у intake_agent.py."""
    return AgentResponse(
        agent="IntakeAgent",
        content="Intake already seeded in demo.",
        next_action="handoff",
        handoff_to="KYCAgent",
    )


AGENT_REGISTRY: dict[str, Callable[[Application, str], AgentResponse]] = {
    "IntakeAgent": intake_agent_mock,
    "KYCAgent": kyc_agent,
    "BureauAgent": bureau_agent,
    "UnderwriterAgent": underwriter_agent,
}


# ────────────────────────── orchestrator loop ──────────────────────────


def _apply_changes(state: Application, data: dict) -> Application:
    """Застосувати AgentResponse.data до Application."""
    if not data:
        return state
    update: dict = {}
    if "kyc_passed" in data:
        update["kyc_passed"] = data["kyc_passed"]
    if "bureau_score" in data:
        update["bureau_score"] = data["bureau_score"]
    if "decision" in data:
        update["decision"] = data["decision"]
    if "rate_apr" in data:
        update["rate_apr"] = data["rate_apr"]
    return state.model_copy(update=update)


@dataclass
class StepLog:
    agent: str
    content: str
    next_action: str


def run_flow(initial: Application, verbose: bool = True) -> tuple[Application, list[StepLog]]:
    """Прокручує весь flow від поточного стану до terminal."""
    state = initial
    log: list[StepLog] = []

    while True:
        next_agent = route(state)
        if next_agent is None:
            if verbose:
                print(f"[Orchestrator] flow terminated · state.decision={state.decision}")
            break

        if verbose:
            print(f"\n[Orchestrator] → {next_agent}")

        handler = AGENT_REGISTRY[next_agent]
        response = handler(state, user_msg="")
        state = _apply_changes(state, response.data)

        if verbose:
            print(f"  {response.agent}: {response.content}")

        log.append(StepLog(response.agent, response.content, response.next_action))

        if response.next_action == "escalate":
            if verbose:
                print("[Orchestrator] escalation · flow terminated")
            break

    return state, log


# ──────────────────────────────── demo ────────────────────────────────


def _demo() -> None:
    print("=" * 60)
    print("Loan Copilot · W1 Orchestrator demo")
    print("=" * 60)

    # Happy path
    print("\n--- CASE 1: happy path (score 720, KYC pass) ---")
    final, _ = run_flow(SAMPLE_APPLICATION)
    print(f"\nFinal: decision={final.decision} rate_apr={final.rate_apr}")

    # KYC fail
    print("\n" + "=" * 60)
    print("--- CASE 2: KYC fail (sanctions hit) ---")
    bad_kyc = SAMPLE_APPLICATION.model_copy(deep=True)
    bad_kyc.applicant.tax_id = "5555555555"
    final, _ = run_flow(bad_kyc)
    print(f"\nFinal: kyc_passed={final.kyc_passed} decision={final.decision}")

    # Decline
    print("\n" + "=" * 60)
    print("--- CASE 3: low score (580) ---")
    low = SAMPLE_APPLICATION.model_copy(deep=True)
    low.applicant.tax_id = "9876543210"
    final, _ = run_flow(low)
    print(f"\nFinal: decision={final.decision}")


if __name__ == "__main__":
    _demo()
