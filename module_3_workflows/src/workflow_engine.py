"""Durable Workflow Engine для Loan Copilot (Module 3).

Що тут справжнє (не симуляція):
1. Скінченний автомат на LangGraph із ПЕРСИСТЕНТНИМ чекпоінтером (SQLite-файл): стан переживає
   смерть процесу, у тому числі `kill -9`, і відновлюється за `thread_id` у НОВОМУ процесі.
2. Паузи через `interrupt()` + `Command(resume=...)`: комітет (HITL, Tier C) і очікування e-sign вебхука.
   Після відновлення повторно виконується лише вузол, що чекав, а не весь процес з intake.
3. Ідемпотентний бар'єр у БД: ключ SHA256(application_id + node + event_id), `PRIMARY KEY`;
   дубль вебхука не породжує другої виплати; виплата сама захищена `PRIMARY KEY (application_id)`.
4. Saga: при скасуванні виконуються компенсації cancel_contract → release_limit над реальною таблицею.

Що тут СТАБ: «ядро банку» (резерв ліміту, договір, виплата) — таблиці SQLite (`Ledger`), а не справжня
АБС; скоринг — таблиця з двох тестових ІПН. Це спрощений референс на 10 вузлів, не канонічні 12 з deck
(немає окремих wait-вузлів docs_requested/offer, analyst_review, verify_before_send; немає retry/timeout/DLQ).

Запуск (три реальні процеси; другий вбивається SIGKILL):
    python -m module_3_workflows.src.workflow_engine demo
Окремі кроки:
    python -m module_3_workflows.src.workflow_engine start hitl [--kill]
    python -m module_3_workflows.src.workflow_engine resume hitl approve
    python -m module_3_workflows.src.workflow_engine event hitl signed --event-id evt-1
    python -m module_3_workflows.src.workflow_engine status hitl
"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "data" / "runtime"


# ────────────────────────────── Ledger (стаб ядра банку в SQLite) ──────────────────────────────


class Ledger:
    """Таблиці, що імітують ядро банку: резерв ліміту, договір, виплата, оброблені події."""

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS limits(application_id TEXT PRIMARY KEY, amount REAL, state TEXT);
            CREATE TABLE IF NOT EXISTS contracts(application_id TEXT PRIMARY KEY, contract_id TEXT, state TEXT);
            CREATE TABLE IF NOT EXISTS disbursements(application_id TEXT PRIMARY KEY, amount REAL, ts REAL);
            CREATE TABLE IF NOT EXISTS processed_events(key TEXT PRIMARY KEY, ts REAL);
            """
        )

    def reserve_limit(self, app_id: str, amount: float) -> None:
        self.conn.execute("INSERT OR IGNORE INTO limits VALUES(?,?, 'reserved')", (app_id, amount))

    def create_contract(self, app_id: str, contract_id: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO contracts VALUES(?,?, 'active')", (app_id, contract_id))

    def void_contract(self, app_id: str) -> None:
        self.conn.execute("UPDATE contracts SET state='voided' WHERE application_id=?", (app_id,))

    def release_limit(self, app_id: str) -> None:
        self.conn.execute("UPDATE limits SET state='released' WHERE application_id=?", (app_id,))

    def disburse(self, app_id: str, amount: float) -> bool:
        """True, якщо виплату виконано вперше; друга спроба неможлива через PRIMARY KEY."""
        try:
            self.conn.execute("INSERT INTO disbursements VALUES(?,?,?)", (app_id, amount, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False

    def seen_event(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM processed_events WHERE key=?", (key,)).fetchone() is not None

    def claim_event(self, key: str) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO processed_events VALUES(?,?)", (key, time.time()))
        return cur.rowcount == 1

    def snapshot(self, app_id: str) -> dict[str, Any]:
        one = lambda q: (self.conn.execute(q, (app_id,)).fetchone() or (None,))[0]  # noqa: E731
        return {
            "limit": one("SELECT state FROM limits WHERE application_id=?"),
            "contract": one("SELECT state FROM contracts WHERE application_id=?"),
            "disbursements": one("SELECT COUNT(*) FROM disbursements WHERE application_id=?"),
        }


def event_key(application_id: str, node: str, event_id: str) -> str:
    """Канонічний ключ ідемпотентності: SHA256(application_id + node + event_id)."""
    return hashlib.sha256(f"{application_id}:{node}:{event_id}".encode()).hexdigest()[:16]


# ────────────────────────────── State Model ──────────────────────────────


class LoanWorkflowState(TypedDict, total=False):
    application_id: str
    applicant_name: str
    tax_id: str
    amount: float
    term_months: int
    monthly_income: float
    consent_id: str | None
    is_fop: bool
    fop_months: int
    status: Literal["in_progress", "awaiting_hitl", "approved", "declined", "cancelled", "disbursed"]
    current_node: str
    eligibility_passed: bool | None
    kyc_passed: bool | None
    credit_score: int | None
    monthly_payment: float | None
    pti: float | None
    tier: str | None
    rate_apr: float | None
    hitl_required: bool
    human_decision: str | None
    human_comment: str | None
    contract_id: str | None
    limit_reserved: bool
    signed: bool
    sign_outcome: str | None
    compensation_executed: bool
    citations: list[str]
    audit_trail: list[str]


def new_application(app_id: str, **overrides: Any) -> LoanWorkflowState:
    """Початковий стан заявки (дефолт — happy path: ІПН зі скором 720)."""
    state: LoanWorkflowState = {
        "application_id": app_id,
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
        "hitl_required": False,
        "limit_reserved": False,
        "signed": False,
        "compensation_executed": False,
        "citations": [],
        "audit_trail": [],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _audit(state: LoanWorkflowState, *items: str) -> list[str]:
    return state.get("audit_trail", []) + [f"{time.strftime('%X')} {i}" for i in items]


# ────────────────────────────── Nodes ──────────────────────────────


def intake_node(state: LoanWorkflowState) -> dict[str, Any]:
    rprint(f"[cyan]▶ intake: Заявка {state['application_id']}: перевірка вхідних реквізитів[/cyan]")
    return {"status": "in_progress", "current_node": "intake", "audit_trail": _audit(state, "intake validated")}


def eligibility_node(state: LoanWorkflowState) -> dict[str, Any]:
    rprint("[cyan]▶ eligibility: Перевірка лімітів та правил кредитування (POL-LOAN-01)[/cyan]")
    amount = state["amount"]
    if amount < 10000 or amount > 300000:
        return {"eligibility_passed": False, "status": "declined", "current_node": "eligibility",
                "citations": state.get("citations", []) + ["POL-LOAN-01 §3.1 (сума поза лімітом 10k–300k)"]}
    if state.get("is_fop") and amount > 100000 and state.get("fop_months", 24) < 12:
        return {"eligibility_passed": False, "status": "declined", "current_node": "eligibility",
                "citations": state.get("citations", []) + ["POL-LOAN-01 §4.2.1 (ФОП >100k вимагає стаж ≥ 12 міс)"]}
    return {"eligibility_passed": True, "current_node": "eligibility",
            "citations": state.get("citations", []) + ["POL-LOAN-01 §2.1", "POL-LOAN-01 §3.1"]}


def kyc_node(state: LoanWorkflowState) -> dict[str, Any]:
    rprint("[cyan]▶ kyc: Санкційний скринінг та PEP (тестовий список)[/cyan]")
    if state["tax_id"] == "5555555555":
        return {"kyc_passed": False, "status": "declined", "current_node": "kyc",
                "citations": state.get("citations", []) + ["POL-LOAN-01 §2.1 (санкційний список)"]}
    return {"kyc_passed": True, "current_node": "kyc", "audit_trail": _audit(state, "kyc passed")}


def credit_history_node(state: LoanWorkflowState) -> dict[str, Any]:
    rprint("[cyan]▶ credit_history: Запит кредитної історії (Consent Gate)[/cyan]")
    consent = state.get("consent_id")
    if not consent or len(consent) < 8:
        return {"credit_score": None, "status": "declined", "current_node": "credit_history",
                "audit_trail": _audit(state, "blocked: missing consent")}
    score = {"1234567890": 720, "9876543210": 580}.get(state["tax_id"], 650)  # стаб бюро
    return {"credit_score": score, "current_node": "credit_history", "audit_trail": _audit(state, f"score {score}")}


def underwriter_node(state: LoanWorkflowState) -> dict[str, Any]:
    rprint("[cyan]▶ underwriter: PMT/PTI та 4-Tier матриця (SOP-CREDIT-02) — детермінований код[/cyan]")
    score = state.get("credit_score") or 0
    amount = Decimal(str(state["amount"]))
    term = state["term_months"]
    income = Decimal(str(state["monthly_income"]))
    r = float(Decimal("18.5") / Decimal("100") / Decimal("12"))
    factor = (r * (1.0 + r) ** term) / (((1.0 + r) ** term) - 1.0)
    pmt = (amount * Decimal(str(factor))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pti = (pmt / income).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    if score >= 750 and pti <= Decimal("0.35"):
        tier, dec, rate, hitl = "Tier A", "approved", 15.9, False
    elif score >= 650 and pti <= Decimal("0.45"):
        tier, dec, rate, hitl = "Tier B", "approved", 18.5, False
    elif score >= 550 and pti <= Decimal("0.50"):
        tier, dec, rate, hitl = "Tier C", "awaiting_hitl", 24.0, True
    else:
        tier, dec, rate, hitl = "Tier D", "declined", None, False
    return {"monthly_payment": float(pmt), "pti": float(pti), "tier": tier, "rate_apr": rate, "status": dec,
            "hitl_required": hitl, "current_node": "underwriter",
            "citations": state.get("citations", []) + [f"SOP-CREDIT-02 §3.1 ({tier})"]}


def hitl_committee_node(state: LoanWorkflowState) -> dict[str, Any]:
    """Справжня пауза: interrupt() зберігає чекпоінт і зупиняє граф до Command(resume=...)."""
    rprint("[bold yellow]⏸ committee: Tier C: чекаємо рішення андеррайтера (interrupt)[/bold yellow]")
    answer = interrupt({"type": "committee_review", "application_id": state["application_id"],
                        "tier": state.get("tier"), "pti": state.get("pti")})
    decision = (answer or {}).get("decision")
    comment = (answer or {}).get("comment")
    if decision == "approve":
        return {"status": "approved", "hitl_required": False, "current_node": "hitl_committee",
                "human_decision": decision, "human_comment": comment,
                "audit_trail": _audit(state, "human_override_approved")}
    return {"status": "declined", "hitl_required": False, "current_node": "hitl_committee",
            "human_decision": decision or "decline", "human_comment": comment,
            "audit_trail": _audit(state, "human_override_declined")}


def make_contract_node(ledger: Ledger):
    def contract_node(state: LoanWorkflowState) -> dict[str, Any]:
        app_id = state["application_id"]
        rprint(f"[cyan]▶ contract: Договір і резерв ліміту {state['amount']:,.2f} грн (Saga: пряма дія)[/cyan]")
        contract_id = f"DOG-{app_id}"
        ledger.reserve_limit(app_id, state["amount"])
        ledger.create_contract(app_id, contract_id)
        return {"contract_id": contract_id, "limit_reserved": True, "current_node": "contract",
                "audit_trail": _audit(state, f"contract {contract_id}", "limit reserved")}
    return contract_node


def make_sign_awaiting_node(ledger: Ledger):
    def sign_awaiting_node(state: LoanWorkflowState) -> dict[str, Any]:
        """Очікування вебхука Дія.Підпис: interrupt() + ідемпотентний бар'єр у БД."""
        app_id = state["application_id"]
        rprint("[bold yellow]⏸ sign_awaiting: Чекаємо подію e-sign / скасування (interrupt)[/bold yellow]")
        event = interrupt({"type": "await_sign_event", "application_id": app_id}) or {}
        key = event_key(app_id, "sign_awaiting", str(event.get("event_id", "")))
        if not ledger.claim_event(key):
            rprint(f"[yellow]  ○ Бар'єр: подію {key} уже оброблено — дубль ігноруємо, чекаємо далі[/yellow]")
            return {"sign_outcome": "duplicate", "current_node": "sign_awaiting"}
        outcome = "signed" if event.get("type") == "signed" else "cancelled"
        return {"sign_outcome": outcome, "signed": outcome == "signed", "current_node": "sign_awaiting",
                "audit_trail": _audit(state, f"event {event.get('type')} key={key}")}
    return sign_awaiting_node


def make_disburse_node(ledger: Ledger):
    def disburse_node(state: LoanWorkflowState) -> dict[str, Any]:
        first = ledger.disburse(state["application_id"], state["amount"])
        rprint(f"[bold green]✔ disburse: {'ВИПЛАТУ виконано' if first else 'виплата вже була — пропуск'}: "
               f"{state['amount']:,.2f} грн[/bold green]")
        return {"status": "disbursed", "current_node": "disburse",
                "audit_trail": _audit(state, "funds disbursed" if first else "disburse skipped (already paid)")}
    return disburse_node


def make_compensation_node(ledger: Ledger):
    def compensation_node(state: LoanWorkflowState) -> dict[str, Any]:
        app_id = state["application_id"]
        rprint("[bold red]↺ saga: Компенсація: cancel_contract → release_limit[/bold red]")
        ledger.void_contract(app_id)   # канонічний порядок: спершу договір,
        ledger.release_limit(app_id)   # потім резерв ліміту
        return {"status": "cancelled", "limit_reserved": False, "compensation_executed": True,
                "current_node": "compensation",
                "audit_trail": _audit(state, "contract voided", "limit released")}
    return compensation_node


# ────────────────────────────── Routing ──────────────────────────────


def after_eligibility(state: LoanWorkflowState) -> str:
    return "kyc" if state.get("eligibility_passed") else END


def after_kyc(state: LoanWorkflowState) -> str:
    return "credit_history" if state.get("kyc_passed") else END


def after_credit_history(state: LoanWorkflowState) -> str:
    return END if state.get("status") == "declined" or state.get("credit_score") is None else "underwriter"


def after_underwriter(state: LoanWorkflowState) -> str:
    if state.get("hitl_required"):
        return "hitl_committee"
    return "contract" if state.get("status") == "approved" else END


def after_hitl(state: LoanWorkflowState) -> str:
    return "contract" if state.get("status") == "approved" else END


def after_sign(state: LoanWorkflowState) -> str:
    outcome = state.get("sign_outcome")
    if outcome == "signed":
        return "disburse"
    if outcome == "cancelled":
        return "compensation"
    return "sign_awaiting"  # дубль події: лишаємось у очікуванні


# ────────────────────────────── Graph assembly ──────────────────────────────


def build_loan_workflow(checkpointer: Any, ledger: Ledger) -> Any:
    """Граф кредитного конвеєра. Чекпоінтер обов'язковий: без нього interrupt() не працює."""
    wf = StateGraph(LoanWorkflowState)
    wf.add_node("intake", intake_node)
    wf.add_node("eligibility", eligibility_node)
    wf.add_node("kyc", kyc_node)
    wf.add_node("credit_history", credit_history_node)
    wf.add_node("underwriter", underwriter_node)
    wf.add_node("hitl_committee", hitl_committee_node)
    wf.add_node("contract", make_contract_node(ledger))
    wf.add_node("sign_awaiting", make_sign_awaiting_node(ledger))
    wf.add_node("disburse", make_disburse_node(ledger))
    wf.add_node("compensation", make_compensation_node(ledger))

    wf.set_entry_point("intake")
    wf.add_edge("intake", "eligibility")
    wf.add_conditional_edges("eligibility", after_eligibility)
    wf.add_conditional_edges("kyc", after_kyc)
    wf.add_conditional_edges("credit_history", after_credit_history)
    wf.add_conditional_edges("underwriter", after_underwriter)
    wf.add_conditional_edges("hitl_committee", after_hitl)
    wf.add_edge("contract", "sign_awaiting")
    wf.add_conditional_edges("sign_awaiting", after_sign)
    wf.add_edge("disburse", END)
    wf.add_edge("compensation", END)
    return wf.compile(checkpointer=checkpointer)


def open_runtime(db_dir: str | Path = DEFAULT_DB_DIR) -> tuple[Any, Ledger, SqliteSaver]:
    """Відкриває (або створює) SQLite-файли чекпоінтів і ledger; новий процес бачить той самий стан."""
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(db_dir / "checkpoints.db"), check_same_thread=False))
    ledger = Ledger(db_dir / "ledger.db")
    return build_loan_workflow(saver, ledger), ledger, saver


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def pending_interrupt(app: Any, thread_id: str) -> Any:
    """Що чекає граф зараз (payload interrupt) або None, якщо процес завершено."""
    snap = app.get_state(thread_config(thread_id))
    for task in snap.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def deliver_event(app: Any, ledger: Ledger, application_id: str, thread_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Вхід вебхука: дубль відсікається ДО відновлення графа (read-only перевірка); claim — у вузлі."""
    key = event_key(application_id, "sign_awaiting", str(event.get("event_id", "")))
    if ledger.seen_event(key):
        rprint(f"[yellow]○ webhook {event.get('event_id')}: дубль, ігноруємо (ключ {key})[/yellow]")
        return {"duplicate": True}
    return app.invoke(Command(resume=event), config=thread_config(thread_id))


# ────────────────────────────── CLI ──────────────────────────────

SCENARIOS = {
    "happy": {"tax_id": "1234567890"},   # Tier A → без людини, чекає лише e-sign
    "hitl": {"tax_id": "9876543210"},    # скор 580 → Tier C → пауза комітету
    "cancel": {"tax_id": "1234567890"},  # як happy; далі подія cancelled → Saga
}


def _cli_start(args: argparse.Namespace) -> None:
    app, ledger, _ = open_runtime(args.db)
    thread = f"thread-{args.scenario}"
    state = new_application(f"LC-{args.scenario.upper()}", **SCENARIOS[args.scenario])
    app.invoke(state, config=thread_config(thread))
    rprint(f"[bold]pid={os.getpid()}[/bold] чекпоінт збережено, граф чекає: {pending_interrupt(app, thread)}")
    if args.kill:
        rprint("[bold red]✖ kill -9 самому собі (SIGKILL): процес помирає без жодного cleanup[/bold red]")
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGKILL)


def _cli_resume(args: argparse.Namespace) -> None:
    app, ledger, _ = open_runtime(args.db)
    thread = f"thread-{args.scenario}"
    rprint(f"[bold]pid={os.getpid()}[/bold] НОВИЙ процес; граф чекає: {pending_interrupt(app, thread)}")
    app.invoke(Command(resume={"decision": args.decision, "comment": args.comment}), config=thread_config(thread))
    rprint(f"Після відновлення граф чекає: {pending_interrupt(app, thread)}")


def _cli_event(args: argparse.Namespace) -> None:
    app, ledger, _ = open_runtime(args.db)
    deliver_event(app, ledger, f"LC-{args.scenario.upper()}", f"thread-{args.scenario}",
                  {"type": args.type, "event_id": args.event_id})
    rprint(f"Ledger: {ledger.snapshot(f'LC-{args.scenario.upper()}')}")


def _cli_status(args: argparse.Namespace) -> None:
    app, ledger, _ = open_runtime(args.db)
    thread = f"thread-{args.scenario}"
    snap = app.get_state(thread_config(thread))
    rprint({"next": snap.next, "status": snap.values.get("status"), "waiting_for": pending_interrupt(app, thread),
            "ledger": ledger.snapshot(f"LC-{args.scenario.upper()}")})


def _run(module_args: list[str], db: Path) -> int:
    cmd = [sys.executable, "-m", "module_3_workflows.src.workflow_engine", *module_args, "--db", str(db)]
    return subprocess.run(cmd).returncode


def run_demo(db: Path | None = None) -> None:
    """Три реальні процеси: старт → kill -9 → окремий процес відновлює → дубль вебхука → Saga."""
    import shutil
    import tempfile

    db = db or Path(tempfile.mkdtemp(prefix="loan-demo-"))
    if db.exists():
        shutil.rmtree(db)
    console.print(Panel.fit("[bold white]Loan Copilot · Module 3 · реальна відмовостійкість[/bold white]\n"
                            f"[cyan]SQLite checkpointer + interrupt() · БД: {db}[/cyan]", border_style="cyan"))

    rprint("\n[bold yellow]── 1. Процес A: заявка в сірій зоні доходить до комітету і отримує kill -9 ──[/bold yellow]")
    code = _run(["start", "hitl", "--kill"], db)
    rprint(f"Код завершення процесу A: [bold]{code}[/bold] (−9 = SIGKILL)")

    rprint("\n[bold yellow]── 2. Процес B (новий PID): підхоплює чекпоінт, комітет схвалює ──[/bold yellow]")
    _run(["resume", "hitl", "approve", "--comment", "наявний строковий депозит"], db)

    rprint("\n[bold yellow]── 3. Той самий вебхук e-sign приходить тричі (кожен раз новий процес) ──[/bold yellow]")
    for _ in range(3):
        _run(["event", "hitl", "signed", "--event-id", "dia-evt-1"], db)

    rprint("\n[bold yellow]── 4. Saga: інша заявка скасована клієнтом після резерву ліміту ──[/bold yellow]")
    _run(["start", "cancel"], db)
    _run(["event", "cancel", "cancelled", "--event-id", "cancel-evt-1"], db)

    ledger = Ledger(db / "ledger.db")
    table = Table(title="Підсумок у ledger (ядро-стаб)")
    for col in ("Заявка", "Ліміт", "Договір", "Виплат"):
        table.add_column(col)
    for app_id in ("LC-HITL", "LC-CANCEL"):
        s = ledger.snapshot(app_id)
        table.add_row(app_id, str(s["limit"]), str(s["contract"]), str(s["disbursements"]))
    console.print(table)
    rprint("[dim]Очікувано: LC-HITL — 1 виплата попри 3 вебхуки; LC-CANCEL — договір voided, ліміт released, 0 виплат.[/dim]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Loan Copilot durable workflow")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("start", "resume", "event", "status"):
        p = sub.add_parser(name)
        p.add_argument("scenario", choices=list(SCENARIOS))
        p.add_argument("--db", type=Path, default=DEFAULT_DB_DIR)
        if name == "start":
            p.add_argument("--kill", action="store_true", help="SIGKILL одразу після збереження чекпоінта")
        if name == "resume":
            p.add_argument("decision", choices=["approve", "decline"])
            p.add_argument("--comment", default="")
        if name == "event":
            p.add_argument("type", choices=["signed", "cancelled"])
            p.add_argument("--event-id", required=True)
    sub.add_parser("demo")
    args = ap.parse_args()
    if args.cmd in (None, "demo"):
        run_demo()
    else:
        {"start": _cli_start, "resume": _cli_resume, "event": _cli_event, "status": _cli_status}[args.cmd](args)


if __name__ == "__main__":
    main()
