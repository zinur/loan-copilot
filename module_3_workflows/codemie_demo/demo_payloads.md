# W3 demo payloads · 4 scenarios (~15 хв у CodeMie Workflows)

> На відміну від W1/W2, тут ми запускаємо через **Workflows →
> Start Execution** (НЕ parent chat), бо потрібні durable waits
> з resume через webhook.

## Pre-requisites

- Workflow `loan-copilot-durable-w3` deployed (див. HOW_TO_DEPLOY.md).
- Checkpointer: Postgres (Memory втратить state при demo pause).
- Webhook endpoints доступні: `docs.uploaded`, `offer.decision`, `contract.signed`.
- Admin → Workflows → Execution tab відкрита (для показу pause/resume).

---

## 1. Happy path · survive kill -9 · ~5 хв

### Start

```json
{
  "message": "Марія Коваленко, 1234567890, +380501234567, maria@example.com, дохід 35000, сума 150000, строк 24, consumer.",
  "consent_id": "consent-demo-01"
}
```

**Workflow starts** → intake → **PAUSE at docs-requested-state** (чекає webhook).

### Simulate k8s restart

У CodeMie Admin → Workflows → Executions → copy `thread_id`.
**Перезапусти worker pod:** `kubectl delete pod codemie-workflow-worker-xxx`.

Через 10 секунд pod відновлюється. У Executions tab — твій workflow
все ще у статусі `interrupted:docs-requested-state`. **State persistence
через checkpointer працює.**

### Resume з webhook

```bash
curl -X POST \
  http://codemie.<pb-internal>/api/v1/workflows/<workflow_id>/executions/<thread_id>/resume \
  -H 'Content-Type: application/json' \
  -d '{"docs_uploaded": true, "doc_urls": ["s3://pb/intake/doc1.pdf", "s3://pb/intake/doc2.pdf"]}'
```

**Flow продовжується:** docs-processing (OCR confidence 0.92) → kyc →
credit-history → underwriter (band A) → contract-draft → verify
(pass) → offer-state **PAUSE**.

### Simulate 2-day wait (resume з offer.decision)

```bash
curl -X POST .../resume \
  -d '{"client_decision": "accept"}'
```

→ sign-awaiting-state **PAUSE**.

### Webhook contract.signed

```bash
curl -X POST .../resume \
  -d '{"disbursed": true, "disburse_id": "DISB-...", "event_key": "sha256-...", "was_duplicate": false}'
```

→ workflow ends: `disbursed=true`.

**Що показуємо залу:** workflow прожив `kill -9` pod'а + 2-денну паузу +
3 webhook resumes. Нуль втрачених заявок.

---

## 2. Idempotent webhook replay · ~3 хв

### Setup

Повтори Scenario 1 до того, як дійшов до sign-awaiting-state.
Workflow PAUSED at sign-awaiting.

### First resume (normal)

```bash
curl -X POST .../resume \
  -d '{"disbursed": true, "disburse_id": "DISB-001", "event_key": "sha256-abc123", "was_duplicate": false}'
```

→ money disbursed. Workflow ended.

### Replay same webhook (Дія.Підпис retry)

```bash
curl -X POST .../resume \
  -d '{"disbursed": true, "disburse_id": "DISB-001", "event_key": "sha256-abc123", "was_duplicate": false}'
```

**Очікуване:** CodeMie повертає 200 OK, але з ignored flag або 409 Conflict
(execution already completed, state=disbursed).

**Жодного другого disburse** — ніяких 150 000 UAH × 2.

**Що говориш:** "Два рівні захисту: (1) CodeMie workflow execution state
дозволяє resume тільки якщо status=interrupted. (2) Якщо LLM якимось
чином re-entry на цей state (edge case), idempotency key у tool
`check_idempotency(event_key)` поверне cached result."

---

## 3. Saga rollback · client cancellation · ~4 хв

### Setup

Запусти Scenario 1 до offer-state (contract reserved).

### Client rejects offer

```bash
curl -X POST .../resume \
  -d '{"client_decision": "reject"}'
```

**Workflow transitions:** offer-state → `compensation-state`.

**У compensation-state видно (через Workflows → Executions → view):**
1. `limit.release(contract_id=LC-...)` → status: ok.
2. `contract.cancel(contract_id=LC-...)` → status: ok.
3. `notify.send(tax_id=1234567890, template=offer_rejected)` → status: ok.
4. 3 audit events written.

**Що говориш:** "Saga — не magic. Це checklist з compensating actions,
викликаних у reverse order. У банку кожна side-effect action
(резерв, договір, SMS) має парну rollback action. 2-Phase Commit тут
не працює — XA не існує для SMS gateway."

---

## 4. Pre-write gate fail · verify blocks bad contract · ~3 хв

### Setup

Прекрасний юзер з happy-path даних, але **штучно підкрутимо**
contract-draft tool, щоб повернув wrong rate_apr:

У `tool_contracts.yaml` змінити `contract.draft` mock response для
`tax_id=1234567890` — повертати `rate_apr=22.0` замість `18.5`.

### Run

```json
{"message": "Марія Коваленко, 1234567890, ..., ..."}
```

Пройде intake → eligibility → consent → kyc → credit → underwriter
(`rate_apr=18.5`) → contract-draft (**поверне 22.0, bug**) →
**verify-before-send FAIL**:

```json
{
  "verify_passed": false,
  "discrepancies": [
    "rate_mismatch: contract_rate_apr_from_text=22.0 but underwriter_rate_apr=18.5"
  ],
  "escalation_level": "P0"
}
```

→ `compensation-state` → `limit.release` + `contract.cancel` + notify
клієнту "технічна затримка".

**Audit:** `rate_mismatch` → engineering P0 ticket.

**Що говориш:** "Це saved Air Canada case. Underwriter порахував 18.5%,
template Jinja підставив 22% (bug у template renderer). Клієнт **ніколи
не отримає цей договір**, тому що pre-write deterministic gate ловить
mismatch між LLM output та final artifact. Engineering fix bug у template,
клієнт — SMS 'технічна затримка, скоро повернемось'."

---

## Відкриті вкладки для демо (facilitator)

1. **Admin → Workflows → `loan-copilot-durable-w3`** — для показу YAML
   конфігурації.
2. **Admin → Workflows → Executions** — live view запущених workflows з
   status (running / interrupted / completed / failed).
3. **Terminal** — для `curl` resume calls.
4. **k9s або `kubectl get pods -n codemie`** — для показу `kill -9`.
5. **Langfuse trace view** — для post-mortem аналізу traces.
