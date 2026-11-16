"""Тести durable workflow: реальний SQLite-чекпоінтер, interrupt/resume, ідемпотентність, Saga, kill -9."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from langgraph.types import Command

from module_3_workflows.src.workflow_engine import (
    deliver_event,
    new_application,
    open_runtime,
    pending_interrupt,
    thread_config,
)

ROOT = Path(__file__).resolve().parents[2]


def _start(db: Path, scenario_tax_id: str, app_id: str, thread: str):
    app, ledger, saver = open_runtime(db)
    app.invoke(new_application(app_id, tax_id=scenario_tax_id), config=thread_config(thread))
    return app, ledger


def test_happy_path_waits_for_sign_then_disburses(tmp_path):
    """Tier A: без людини, але чекає e-sign (interrupt); після події — одна виплата."""
    app, ledger = _start(tmp_path, "1234567890", "LC-T1", "t1")
    assert pending_interrupt(app, "t1")["type"] == "await_sign_event"
    assert ledger.snapshot("LC-T1") == {"limit": "reserved", "contract": "active", "disbursements": 0}

    res = deliver_event(app, ledger, "LC-T1", "t1", {"type": "signed", "event_id": "e1"})
    assert res["status"] == "disbursed"
    assert ledger.snapshot("LC-T1")["disbursements"] == 1


def test_hitl_pause_survives_process_restart(tmp_path):
    """Tier C зупиняється на комітеті; НОВИЙ граф+чекпоінтер на тому ж файлі продовжує з місця паузи."""
    app, _ = _start(tmp_path, "9876543210", "LC-T2", "t2")
    assert pending_interrupt(app, "t2")["type"] == "committee_review"

    app2, ledger2, _ = open_runtime(tmp_path)  # «новий процес»: нові об'єкти, той самий диск
    assert pending_interrupt(app2, "t2")["type"] == "committee_review"
    app2.invoke(Command(resume={"decision": "approve", "comment": "депозит"}), config=thread_config("t2"))

    assert pending_interrupt(app2, "t2")["type"] == "await_sign_event"
    values = app2.get_state(thread_config("t2")).values
    assert values["human_decision"] == "approve"
    assert any("human_override_approved" in a for a in values["audit_trail"])
    assert ledger2.snapshot("LC-T2")["contract"] == "active"


def test_hitl_decline_stops_without_side_effects(tmp_path):
    app, ledger = _start(tmp_path, "9876543210", "LC-T3", "t3")
    res = app.invoke(Command(resume={"decision": "decline"}), config=thread_config("t3"))
    assert res["status"] == "declined"
    assert ledger.snapshot("LC-T3") == {"limit": None, "contract": None, "disbursements": 0}


def test_saga_compensation_on_cancel(tmp_path):
    """Скасування після резерву: договір voided, ліміт released, виплати немає."""
    app, ledger = _start(tmp_path, "1234567890", "LC-T4", "t4")
    res = deliver_event(app, ledger, "LC-T4", "t4", {"type": "cancelled", "event_id": "c1"})
    assert res["status"] == "cancelled" and res["compensation_executed"] is True
    assert ledger.snapshot("LC-T4") == {"limit": "released", "contract": "voided", "disbursements": 0}


def test_duplicate_webhook_is_idempotent(tmp_path):
    """Три однакові вебхуки (кожен через новий граф) — рівно одна виплата."""
    app, ledger = _start(tmp_path, "1234567890", "LC-T5", "t5")
    event = {"type": "signed", "event_id": "dup-1"}
    deliver_event(app, ledger, "LC-T5", "t5", event)
    for _ in range(2):
        app_n, ledger_n, _ = open_runtime(tmp_path)
        out = deliver_event(app_n, ledger_n, "LC-T5", "t5", event)
        assert out == {"duplicate": True}
    assert ledger.snapshot("LC-T5")["disbursements"] == 1


def test_disburse_is_idempotent_even_without_ingress_check(tmp_path):
    """Другий захист: PRIMARY KEY у таблиці виплат не дозволить подвійну виплату."""
    _, ledger = _start(tmp_path, "1234567890", "LC-T6", "t6")
    assert ledger.disburse("LC-T6", 100.0) is True
    assert ledger.disburse("LC-T6", 100.0) is False


def test_real_sigkill_between_processes(tmp_path):
    """Справжній kill -9: процес A помирає після чекпоінта, процес B (окремий PID) відновлює стан."""
    base = [sys.executable, "-m", "module_3_workflows.src.workflow_engine"]
    a = subprocess.run([*base, "start", "hitl", "--kill", "--db", str(tmp_path)], cwd=ROOT, capture_output=True)
    assert a.returncode == -9  # SIGKILL

    app, ledger, _ = open_runtime(tmp_path)
    assert pending_interrupt(app, "thread-hitl")["type"] == "committee_review"

    b = subprocess.run([*base, "resume", "hitl", "approve", "--db", str(tmp_path)], cwd=ROOT, capture_output=True)
    assert b.returncode == 0
    app2, _, _ = open_runtime(tmp_path)
    assert pending_interrupt(app2, "thread-hitl")["type"] == "await_sign_event"
