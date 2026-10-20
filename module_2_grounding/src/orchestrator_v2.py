"""Orchestrator V2 · Grounded Supervisor Flow з RAG, Tool Contracts та Consent Gate.

Нові можливості W2 порівняно з W1:
1. EligibilityAgent з RAG-перевіркою за базою знань (POL-LOAN-01, REG-NBU-351) та обов'язковими цитатами
2. Справжні контракти інструментів (kyc.lookup, credit_history.pull, risk.score)
3. Юридичний Consent Gate: блокування запиту кредитної історії без дійсного consent_id
4. Точна детермінована фінансова математика (ануїтет PMT та PTI) без LLM-галюцинацій
5. Незмінний транзакційний аудит у файл audit.jsonl

Запуск демо:
    python -m module_2_grounding.src.orchestrator_v2
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common.scenario_data import SAMPLE_APPLICATION
from common.schemas import Application
from .eligibility_agent import EligibilityAgent, EligibilityResult
from .tool_contracts import (
    ConsentRequiredException,
    UnauthorizedRoleException,
    calculate_loan_math,
    credit_history_pull,
    evaluate_risk_tier,
    kyc_lookup,
)

console = Console()


@dataclass
class FlowStateV2:
    application: Application
    applicant_age: int = 28
    is_fop: bool = False
    fop_registration_months: int = 24
    consent_id: str | None = "consent-uuid-2026-auth-mariia"
    eligibility: EligibilityResult | None = None
    kyc_passed: bool | None = None
    credit_score: int | None = None
    monthly_payment: Decimal | None = None
    pti: Decimal | None = None
    tier: str | None = None
    decision: Literal["approve", "decline", "manual_review"] | None = None
    rate_apr: Decimal | None = None
    citations: list[str] = field(default_factory=list)
    audit_events: list[str] = field(default_factory=list)


class OrchestratorV2:
    """Оркестратор W2 з повною підтримкою заземлення та безпеки інструментів."""

    def __init__(self, use_api: bool = False) -> None:
        self.eligibility_agent = EligibilityAgent(use_api=use_api)

    def run(self, state: FlowStateV2, verbose: bool = True) -> FlowStateV2:
        app = state.application
        if verbose:
            rprint(f"\n[bold white]═══ Запуск заявки {app.application_id} ({app.applicant.full_name}) ═══[/bold white]")

        # ── КРОК 1: Eligibility Agent (RAG по регламентах) ──
        if verbose:
            rprint("[cyan]▶ Крок 1: EligibilityAgent (RAG-пошук за POL-LOAN-01 та REG-NBU-351)[/cyan]")
        
        el_res = self.eligibility_agent.evaluate(
            app=app,
            applicant_age=state.applicant_age,
            is_fop=state.is_fop,
            fop_registration_months=state.fop_registration_months,
        )
        state.eligibility = el_res
        state.citations.extend(el_res.citations)

        if not el_res.is_eligible:
            state.decision = "decline"
            if verbose:
                rprint(f"  [red]✖ Eligibility REJECT:[/red] {el_res.reason} (Цитати: {', '.join(el_res.citations)})")
            return state

        if verbose:
            rprint(f"  [green]✔ Eligibility PASS:[/green] {el_res.reason} (Цитати: {', '.join(el_res.citations)})")

        # ── КРОК 2: KYC Agent (Виклик інструменту kyc.lookup) ──
        if verbose:
            rprint("[cyan]▶ Крок 2: KYCAgent (Виклик tool kyc.lookup · перевірка санкцій та PEP)[/cyan]")
        
        try:
            kyc_res = kyc_lookup(tax_id=app.applicant.tax_id, caller_role="kyc_officer")
            state.kyc_passed = kyc_res.passed
            if not kyc_res.passed:
                state.decision = "decline"
                if verbose:
                    rprint(f"  [red]✖ KYC FAIL:[/red] {kyc_res.reason} · Зупинка конвеєра")
                return state
            if verbose:
                rprint(f"  [green]✔ KYC PASS:[/green] {kyc_res.reason} (Cost: ${kyc_res.cost_usd:.2f})")
        except UnauthorizedRoleException as e:
            state.decision = "decline"
            rprint(f"  [bold red]Порушення безпеки RBAC:[/bold red] {e}")
            return state

        # ── КРОК 3: CreditHistory Agent (Виклик credit_history.pull з Consent Gate) ──
        if verbose:
            rprint("[cyan]▶ Крок 3: CreditHistoryAgent (Виклик tool credit_history.pull · перевірка Consent Gate)[/cyan]")

        try:
            ch_res = credit_history_pull(
                tax_id=app.applicant.tax_id,
                consent_id=state.consent_id,
                caller_role="loan_officer",
            )
            state.credit_score = ch_res.credit_score
            if verbose:
                rprint(f"  [green]✔ CreditHistory PASS:[/green] Скор = {ch_res.credit_score} балів (Cost: ${ch_res.cost_usd:.2f})")
        except ConsentRequiredException as e:
            state.decision = "decline"
            if verbose:
                rprint(f"  [bold red]✖ БЛОКУВАННЯ CONSENT GATE:[/bold red] {e}")
            return state
        except UnauthorizedRoleException as e:
            state.decision = "decline"
            rprint(f"  [bold red]Порушення безпеки RBAC:[/bold red] {e}")
            return state

        # ── КРОК 4: Underwriter Agent (Python Math + 4-Tier Policy) ──
        if verbose:
            rprint("[cyan]▶ Крок 4: UnderwriterAgent (Детермінований розрахунок ануїтету + 4-Tier SOP-CREDIT-02)[/cyan]")

        # 4.1. Точний математичний розрахунок (без LLM!)
        math_res = calculate_loan_math(
            amount=app.request.amount,
            term_months=app.request.term_months,
            monthly_income=app.applicant.monthly_income,
            annual_rate_pct=Decimal("18.5"),
        )
        state.monthly_payment = math_res.monthly_payment
        state.pti = math_res.pti

        # 4.2. Оцінка матриці ризику за SOP-CREDIT-02 §3.1
        risk_res = evaluate_risk_tier(
            credit_score=state.credit_score,
            pti=math_res.pti,
            caller_role="underwriter",
        )
        state.tier = risk_res.tier
        state.decision = risk_res.decision
        state.rate_apr = risk_res.rate_apr
        state.citations.append(risk_res.rule_matched)

        if verbose:
            rprint(f"  [green]✔ Математика:[/green] Щомісячний платіж PMT = {math_res.monthly_payment:,.2f} грн · PTI = {math_res.pti * 100:.1f}%")
            rprint(f"  [bold green]✔ Рішення Андеррайтера:[/bold green] {risk_res.decision.upper()} ({risk_res.tier}, ставка {risk_res.rate_apr}%)")
            rprint(f"  [dim]Підстава: {risk_res.rule_matched}[/dim]")

        return state


def run_demo() -> None:
    console.print(
        Panel.fit(
            "[bold white]Loan Copilot · Webinar 2 End-to-End Grounded Orchestrator[/bold white]\n"
            "[cyan]Повний конвеєр: RAG на політиках + Tool Contracts + Consent Gate + Точна математика[/cyan]",
            border_style="cyan",
        )
    )

    orch = OrchestratorV2(use_api=False)

    # ─────────────────── СЦЕНАРІЙ 1: Happy Path ───────────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 1: Happy Path (Марія Коваленко, скор 720, згода надана) ──────────[/bold yellow]")
    s1 = FlowStateV2(
        application=SAMPLE_APPLICATION.model_copy(deep=True),
        applicant_age=28,
        is_fop=False,
        consent_id="valid-consent-uuid-mariia-2026",
    )
    res1 = orch.run(s1, verbose=True)

    t1 = Table(title="Підсумковий статус Сценарію 1", show_lines=True)
    t1.add_column("Параметр", style="bold")
    t1.add_column("Значення", style="green")
    t1.add_row("Рішення (Decision)", f"[bold green]{res1.decision.upper()}[/bold green]")
    t1.add_row("Кредитний рейтинг", f"{res1.tier} (Скор: {res1.credit_score})")
    t1.add_row("Щомісячний платіж (PMT)", f"{res1.monthly_payment:,.2f} грн")
    t1.add_row("Коефіцієнт навантаження (PTI)", f"{res1.pti * 100:.1f}%")
    t1.add_row("Річна ставка (APR)", f"{res1.rate_apr}%")
    t1.add_row("Нормативні цитування", " · ".join(res1.citations))
    console.print(t1)

    # ─────────────── СЦЕНАРІЙ 2: Блокування Consent Gate ───────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 2: Захист PII (Спроба витягу кредитної історії БЕЗ consent_id) ──────────[/bold yellow]")
    s2 = FlowStateV2(
        application=SAMPLE_APPLICATION.model_copy(deep=True),
        applicant_age=28,
        is_fop=False,
        consent_id=None,  # Клієнт НЕ дав згоду!
    )
    res2 = orch.run(s2, verbose=True)
    rprint(f"[bold red]Результат:[/bold red] Заявка зупинена, статус: {res2.decision} (Запобігання штрафу 20 млн грн)")

    # ─────────────── СЦЕНАРІЙ 3: Виняток для ФОП (§4.2.1 RAG) ───────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 3: RAG-виняток для ФОП (Сума 150k > 100k, стаж 7 міс < 12 міс) ──────────[/bold yellow]")
    fop_app = SAMPLE_APPLICATION.model_copy(deep=True)
    fop_app.request.amount = Decimal("150000")
    s3 = FlowStateV2(
        application=fop_app,
        applicant_age=32,
        is_fop=True,
        fop_registration_months=7,  # Менше 12 місяців!
        consent_id="valid-consent-uuid-fop-2026",
    )
    res3 = orch.run(s3, verbose=True)
    rprint(f"[bold red]Результат:[/bold red] Заявка відхилена на етапі Eligibility за цитатою: {res3.citations}")

    # ─────────────── СЦЕНАРІЙ 4: Санкційний позичальник ───────────────
    rprint("\n[bold yellow]────────── СЦЕНАРІЙ 4: Санкційний скринінг (ІПН 5555555555 у списку РНБО) ──────────[/bold yellow]")
    sanction_app = SAMPLE_APPLICATION.model_copy(deep=True)
    sanction_app.applicant.tax_id = "5555555555"
    s4 = FlowStateV2(
        application=sanction_app,
        applicant_age=45,
        is_fop=False,
        consent_id="valid-consent-uuid-sanction-2026",
    )
    res4 = orch.run(s4, verbose=True)
    rprint(f"[bold red]Результат:[/bold red] Заявка зупинена на етапі KYC: {res4.decision}")


if __name__ == "__main__":
    run_demo()
