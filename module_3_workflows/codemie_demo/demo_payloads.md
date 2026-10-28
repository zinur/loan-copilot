# W3 demo payloads · 4 scenarios (~15 хв у CodeMie Workflows)

> На відміну від W1/W2, тут ми запускаємо через **Workflows →
> Start Execution** (НЕ parent chat), бо потрібні durable waits
> з resume через webhook.

## Pre-requisites

- Workflow `loan-copilot-durable-w3-embedded` deployed (див. HOW_TO_DEPLOY.md).
- CodeMie backend на Postgres (checkpointer автоматично persistent).
- Admin → Workflows → Executions tab відкрита (для показу pause/resume).

## CodeMie Workflow API (verified from source)

Base path: `/api/v1/workflows/{workflow_id}/executions`

| Verb | Path | Purpose |
|---|---|---|
| **POST** | `/` | Start new workflow execution |
| **GET** | `/{execution_id}` | Get execution status |
| **PUT** | `/{execution_id}/output` | Set output JSON for paused state (**call BEFORE resume for HITL**) |
| **PUT** | `/{execution_id}/resume` | Resume interrupted execution |
| **PUT** | `/{execution_id}` | Abort interrupted execution |

**HITL resume flow — ДВА кроки:**
1. `PUT /output` з body `{"state_id": "...", "output": "<JSON-string>"}` —
   встановлює що paused state "видав би".
2. `PUT /resume` з empty body `{}` або `{"user_input": "...", "file_names": []}` —
   продовжує flow.

**Auth:** `Authorization: Bearer <TOKEN>` header. Токен бери з CodeMie
UI → Profile → API Tokens (або PAT з SSO identity provider).

## Shell variables для прикладів

```bash
export CODEMIE_HOST="https://codemie.<pb-internal>"
export CODEMIE_TOKEN="<your-bearer-token>"
export WF_ID="loan-copilot-durable-w3-embedded"
export EXEC_ID="<copy-from-Executions-tab-after-start>"
```

---

## 1. Happy path · survive kill -9 · ~5 хв

### 1a. Start execution (POST)

```bash
curl -X POST \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Марія Коваленко, 1234567890, +380501234567, maria@example.com, дохід 35000, сума 80000, строк 24 міс, consumer. Веду ФОП 8 місяців. Згоден на запит УБКІ.",
    "stream": false
  }'
```

Response → `{execution_id, workflow_id, overall_status: "running", ...}`.
**Копіюй `execution_id` у `$EXEC_ID`.**

Workflow starts → intake → **PAUSE at `docs-requested-state`** (чекає webhook).

### 1b. Simulate k8s restart

У CodeMie Admin → Workflows → Executions → побачиш `$EXEC_ID` зі
статусом `interrupted:docs-requested-state`.

**Перезапусти worker pod:**
```bash
kubectl delete pod -n codemie -l app=codemie-workflow-worker
```

Через 10 секунд pod відновлюється. Executions tab показує той самий
workflow у тому самому interrupted state. **State persistence через
built-in Postgres checkpointer працює.**

### 1c. Resume with "docs uploaded" webhook (TWO-STEP)

Step 1 — set output of the paused state:
```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "state_id": "docs-requested-state",
    "output": "{\"docs_uploaded\": true, \"doc_urls\": [\"s3://pb/intake/doc1.pdf\", \"s3://pb/intake/doc2.pdf\"]}"
  }'
```

Step 2 — resume execution:
```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Flow продовжується:** docs-processing (OCR confidence 0.92) → kyc →
credit-history → underwriter (band A) → contract-draft → verify
(pass) → **PAUSE at `offer-state`**.

### 1d. Resume з client acceptance (TWO-STEP)

```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "state_id": "offer-state",
    "output": "{\"client_decision\": \"accept\"}"
  }'

curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

→ **PAUSE at `sign-awaiting-state`**.

### 1e. Resume з contract.signed webhook (TWO-STEP)

```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "state_id": "sign-awaiting-state",
    "output": "{\"disbursed\": true, \"disburse_id\": \"DISB-001\", \"event_key\": \"sha256-abc123\", \"was_duplicate\": false}"
  }'

curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -d '{}'
```

→ workflow ends: `overall_status=completed`, `disbursed=true`.

**Що показуємо залу:** workflow прожив `kill -9` pod'а + 3 resume cycles.
Нуль втрачених заявок.

---

## 2. Idempotent webhook replay · ~3 хв

### Setup

Повтори Scenario 1 до того, як дійшов до sign-awaiting-state.
Workflow PAUSED at sign-awaiting (не роби resume ще).

### First resume (normal)

```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "state_id": "sign-awaiting-state",
    "output": "{\"disbursed\": true, \"disburse_id\": \"DISB-001\", \"event_key\": \"sha256-abc123\", \"was_duplicate\": false}"
  }'

curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -d '{}'
```

→ money disbursed. `overall_status=completed`.

### Replay same webhook (simulated Дія.Підпис retry)

```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -d '{}'
```

**Очікуване:** CodeMie повертає `409 Conflict` або `400 Bad Request`
("execution not in interrupted state, cannot resume"). Workflow у
`completed`, не `interrupted` — resume блокується на API level.

**Жодного другого disburse** — ніяких 150 000 UAH × 2.

**Що говориш:** "Два рівні захисту: (1) CodeMie workflow executor
дозволяє resume ТІЛЬКИ якщо `overall_status=interrupted`. Після
`completed` — API поверне помилку. (2) Якщо через bug workflow би
знову дійшов до sign-awaiting-state, idempotency key у tool
`check_idempotency(event_key)` повернув би cached result."

---

## 3. Saga rollback · client cancellation · ~4 хв

### Setup

Запусти Scenario 1 до offer-state (contract reserved, limit заблоковано).

### Client rejects offer (TWO-STEP)

```bash
curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "state_id": "offer-state",
    "output": "{\"client_decision\": \"reject\"}"
  }'

curl -X PUT \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -d '{}'
```

**Workflow transitions:** offer-state → `compensation-state`.

**У compensation-state видно (Executions → view execution → state transitions):**
1. `limit.release(contract_id=LC-...)` → status: ok.
2. `contract.cancel(contract_id=LC-...)` → status: ok.
3. `notify.sms(tax_id=1234567890, text="Заявку скасовано, резерв знято.")`.
4. 3 audit events written.

**Що говориш:** "Saga — не магія. Це checklist compensating actions,
викликаних у reverse order. У банку кожна side-effect action (резерв,
договір, SMS) має парну rollback action. 2-Phase Commit тут не
працює — XA не існує для SMS gateway."

---

## 4. Pre-write gate fail · verify blocks bad contract · ~3 хв

### Setup

Happy-path дані, але **штучно підкрутимо** `contract-draft-agent`
system prompt, щоб повернув wrong rate_apr для `tax_id=1234567890`:

У CodeMie Workflow YAML → `contract` assistant → system_prompt → додай
на початок:
```
DEBUG MODE: for tax_id=1234567890 override rate_apr=22.0 (bug simulation).
```
Save → rerun scenario.

### Run

```bash
curl -X POST \
  "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Марія Коваленко, 1234567890, ..., сума 80000...",
    "stream": false
  }'
```

Flow: intake → eligibility → kyc → credit → underwriter (`rate_apr=18.5`)
→ docs-upload (resume as in #1) → OCR → contract-draft (**поверне 22.0, bug**)
→ **verify-before-send FAIL**:

```json
{
  "verify_passed": false,
  "discrepancies": [
    "rate_mismatch: contract_rate_apr=22.0 but underwriter_rate_apr=18.5"
  ]
}
```

→ `compensation-state` → `limit.release` + `contract.cancel` + notify
клієнту "технічна затримка".

**Audit:** `rate_mismatch` → engineering P0 ticket.

**Що говориш:** "Це saved Air Canada case. Underwriter порахував 18.5%,
contract renderer вивів 22% (bug). Клієнт **ніколи не отримає цей
договір**, тому що pre-write deterministic gate ловить mismatch між
LLM output та final artifact. Engineering fix bug, клієнт — SMS
'технічна затримка, скоро повернемось'."

**Cleanup:** приберіть DEBUG MODE override з contract system_prompt.

---

## Відкриті вкладки для демо (facilitator)

1. **Admin → Workflows → `loan-copilot-durable-w3-embedded`** — для показу YAML.
2. **Admin → Workflows → Executions** — live view запущених workflows з
   status (running / interrupted / completed / failed).
3. **Terminal** з `$CODEMIE_HOST`, `$CODEMIE_TOKEN`, `$WF_ID`, `$EXEC_ID`
   env vars налаштованими — для curl commands.
4. **k9s або `kubectl get pods -n codemie`** — для показу `kill -9`.
5. **Langfuse trace view** — post-mortem аналізу.

## Debug tips

- **"400 Bad Request on PUT /output"** — body має містити `state_id` яке
  МАТЧИТЬ exact id останнього paused state (не `state-name`, а `state_id`
  з YAML, наприклад `docs-requested-state`).
- **"output must be a string"** — поле `output` у PUT /output body це
  **JSON string** (escaped), не nested object. `"output": "{\"foo\": \"bar\"}"`
  правильно, `"output": {"foo": "bar"}` — помилка валідації.
- **"execution not in interrupted state"** — resume викликано на
  completed/failed/aborted execution. Перевір `GET /{execution_id}` →
  `overall_status`.
- **401 Unauthorized** — токен expired або неправильний scope. Згенеруй
  новий у CodeMie UI → Profile → API Tokens.
