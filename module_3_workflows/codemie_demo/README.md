# CodeMie live demo · W3 — Durable Loan Copilot (FSM + HITL + Saga)

Готові артефакти для live-демо на W3 вебінарі: конвертуємо W2 chat-based
flow у durable Sequential workflow, що виживає `kill -9` K8s, чекає
клієнта днями, та виконує Saga rollback при cancellation.

## У W3 Option A стає ОСНОВНИМ

На відміну від W1/W2, тут ми переходимо з `assistant_ids` (sub-assistants
через chat) на **Sequential workflow YAML**, тому що:

| Capability | `assistant_ids` chat | Sequential workflow |
|---|---|---|
| 24h/7d durable waits | ❌ | ✅ `interrupt_before: true` + Postgres checkpointer |
| Webhook resume | ❌ | ✅ `/resume` endpoint |
| Survive `kill -9` | ❌ | ✅ State у БД, не у pod memory |
| Per-state retry policies | ❌ | ✅ `retry_policy` на tool-call states |
| Audit trail per node | Partial | ✅ Full state transitions |

## Файли

| Файл | Що це |
|------|-------|
| **[`HOW_TO_DEPLOY.md`](./HOW_TO_DEPLOY.md)** | **Основна інструкція** — 4 нових workers + 6 tools + Postgres checkpointer + webhook endpoints + workflow YAML |
| **[`durable_copilot_workflow.yaml.alt-sequential`](./durable_copilot_workflow.yaml.alt-sequential)** | **Основний артефакт** — 11-state FSM з 3 durable waits, 2 conditional HITL, 1 pre-write gate, 1 Saga compensation |
| [`ws_verifier.prompt.md`](./ws_verifier.prompt.md) | System prompt для Pre-write Evaluator (deterministic match verifier) |
| [`ws_compensator.prompt.md`](./ws_compensator.prompt.md) | System prompt для Saga Compensation agent (3 rollback tools) |
| [`demo_payloads.md`](./demo_payloads.md) | 4 scenarios: happy+survive k9, idempotent replay, Saga rollback, pre-write fail |
| [`demo_script.md`](./demo_script.md) | 15-хв сценарій живого показу |

## 11-state FSM map

```
    1. intake
       ↓ (intake_complete)
    2. docs_requested ⏸ (24-48h durable wait)
       ↓ (docs.uploaded webhook)
    3. docs_processing (OCR, retry 3× з backoff)
       ↓ (confidence >= 0.85 OR to analyst)
    4. analyst_review ◆ (conditional HITL on OCR < 0.85)
       ↓
    5. kyc (W2 tool)
       ↓
    6. credit_history (W2 tool з consent)
       ↓
    7. underwriter (switch: approve → 9, committee → 8, decline → end)
       ↓
    8. committee_review ◆ (conditional HITL for grey zone Tier C)
       ↓
    9. contract_draft (reserves limit — side-effect!)
       ↓
    10. verify_before_send ⚑ (pre-write deterministic gate)
       ↓ (pass OR → compensation)
    11. offer ⏸ (7d durable wait)
       ↓ (accept OR → compensation)
    12. sign_awaiting ⏸ (7d durable wait, idempotent on webhook replay)
       ↓
    END

    X. compensation ⤺ (Saga rollback: limit.release + contract.cancel + notify)
       ← called from docs timeout, analyst reject, verify fail,
         offer reject/expire, sign fail
```

Legend:
- `⏸` durable wait with `interrupt_before: true`
- `◆` conditional HITL (activated only when condition holds)
- `⚑` pre-write evaluator gate
- `⤺` Saga compensation

## Pre-stage checklist (Option A — Sequential workflow)

- [ ] 4 нових W3 workers: `docs-processor-agent`, `contract-draft-agent`,
      `verify-before-send-agent`, `compensation-agent`
- [ ] 6 нових W3 tools: `ocr.extract`, `contract.draft`, `limit.reserve`,
      `limit.release`, `contract.cancel`, `notify.send`
- [ ] CodeMie backend на Postgres (checkpointer автоматично persistent;
      hardcoded у `CheckpointSaver` class — no tenant UI toggle)
- [ ] Workflow `loan-copilot-durable-w3` deployed з YAML
- [ ] 3 webhook endpoints: `docs.uploaded`, `offer.decision`, `contract.signed`
- [ ] Workflow timeouts: docs 48h, analyst 2h, committee 24h, offer 7d, sign 7d
- [ ] Terminal готовий з curl для resume calls
- [ ] k9s / kubectl для `kill -9` демонстрації
- [ ] Admin → Workflows → Executions tab відкрита (live view)
- [ ] Backup: VSCode з `python -m module_3_workflows.src.workflow_engine`

## 3 pattern'и, які зал має запам'ятати

1. **Durable Wait vs sleep.** `interrupt_before: true` → state у Postgres,
   нуль pod resources. `asyncio.sleep` → pod blocked, lost on restart.

2. **Pre-write Evaluator Gate.** Deterministic check МІЖ LLM output та
   final artifact. Saved Air Canada case. Temperature 0, read-only.

3. **Saga Compensation vs 2PC.** 2-Phase Commit mertvий у мікросервісах
   (SMS gateway не має XA). Saga = checklist compensating actions,
   викликаних у reverse order. Один `compensation-state` обслуговує
   усі 4 failure branches (DRY).

## Пов'язано

- Домашнє завдання: [`../02-exercise.md`](../02-exercise.md) — Python
  LangGraph FSM з checkpointer + idempotency + Saga (найважча лаба курсу).
- W2 CodeMie demo (базовий layer): [`../../module_2_grounding/codemie_demo/`](../../module_2_grounding/codemie_demo/).
- W1 CodeMie demo: [`../../module_1_agents/codemie_demo/`](../../module_1_agents/codemie_demo/).
