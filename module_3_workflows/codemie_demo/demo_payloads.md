# W3 demo payloads · 4 scenarios (~15 хв у CodeMie Workflows)

> На відміну від W1/W2, тут ми запускаємо через **Workflows →
> Start Execution** (НЕ parent chat), бо потрібні durable waits
> з resume через webhook.

## Pre-requisites

- Workflow `loan-copilot-durable-w3-embedded` deployed (див. HOW_TO_DEPLOY.md).
- CodeMie backend на Postgres (checkpointer автоматично persistent).
- Admin → Workflows → Executions tab відкрита (для показу pause/resume).

## Два шляхи resume paused state

### Варіант UI (primary для live demo)

Коли workflow паузиться на `interrupt_before: true` state, у Workflow
Execution view з'являються 3 кнопки: **Abort / Edit / Continue**.

**Flow:**
1. **Edit** → відкриває JSON editor для output paused state.
2. Paste очікуваний output (симулює webhook payload) → Save.
3. **Continue** → workflow застосує цей output як "те що state видав би"
   та перейде до наступного.

Цей шлях виглядає інтуїтивно і НЕ потребує curl / terminal / auth
tokens — ідеально для живого демо перед залом SA. Під капотом UI
викликає ті самі `PUT /output` + `PUT /resume` endpoints.

> ⚠ **Important: Edit semantic.** Edit встановлює output для state що
> **blocked by `interrupt_before`** (наступний to run), а не для
> попереднього completed state. UI показує sidebar з completed states +
> highlighted "current" state з Interrupted badge — цей highlighted
> state часто є попереднім completed, а не paused next. Завжди
> дивися на **Input prompt** у центральній панелі — там видно який
> state наступним стартує, і ти саме для нього вводиш output у Edit.
>
> Також: Edit НЕ re-evaluates condition попереднього state. Якщо
> workflow вже routed на branch X (бо попередній state повернув bad
> values), переписати попередній output через Edit не поверне flow
> на branch Y. Для alternate branching роби restart execution.

### Варіант curl / webhook (для production integration)

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

## 1. Happy path · 4 HITL resumes · ~5 хв

### 1a. Start execution — UI (primary для demo)

У CodeMie → Workflows → `loan-copilot-durable-w3-embedded`:

1. Клікни **Run workflow** (НЕ Run Chat — chat mode міняє поведінку).
2. У text area paste client payload:
   ```
   Марія Коваленко, 1234567890, +380501234567, maria@example.com,
   дохід 35000, сума 80000, строк 24 міс, consumer purpose.
   Веду ФОП 8 місяців.
   ```
3. Клікни Submit → workflow стартує.

**Flow:** intake-state ✅ → **PAUSE** before `docs-requested-state`.

> **Що UI показує:** sidebar list completed states (`intake-state ✅`), а
> поточний (paused) state підсвічений + overall status `Interrupted`.
> Внизу три кнопки: **Abort / Edit / Continue**.

### 1b. Resume #1 — docs uploaded (UI)

1. Клікни **Edit** → відкриється JSON editor.
2. Paste output для `docs-requested-state`:
   ```json
   {
     "docs_uploaded": true,
     "doc_urls": ["s3://pb/intake/passport.pdf", "s3://pb/intake/income_cert.pdf"]
   }
   ```
3. Save → клікни **Continue**.

**Flow continues:** docs-processing executes LLM. Оскільки у demo tenant
немає real `ocr.extract` tool, LLM часто повертає `ocr_confidence=0`
та `docs_valid=false` → умова `docs_valid == True and ocr_confidence >= 0.85`
falsy → workflow routes to **analyst-review-state** (conditional HITL).

### 1b.5. Resume #1.5 — analyst approval (UI) ⚠ conditional

Якщо після docs-upload workflow paused і UI показує
**analyst-review-state** у pause (або docs-processing як "last" з
Interrupted badge):

1. **Edit** → paste output для `analyst-review-state`:
   ```json
   {
     "analyst_approved": true,
     "analyst_notes": "Manual OCR verification OK — docs match client profile."
   }
   ```
2. **Continue** → routes to `kyc-state`.

> **ВАЖЛИВО про Edit semantic:** Edit встановлює output для **НАСТУПНОГО
> paused state** (той, що blocked by interrupt_before), а НЕ для
> попереднього completed state. UI може показувати попередній state
> як "current" з Interrupted badge — це just visual pointer. Завжди
> дивися на **Input prompt** paused state'а щоб зрозуміти що саме
> очікується у Edit.

**Flow continues:** kyc-state → **PAUSE** before `consent-awaiting-state`.

### 1c. Resume #2 — client consent via Дія.Підпис (UI) ⭐ W3-specific

1. **Edit** → paste output для `consent-awaiting-state`:
   ```json
   {
     "consent_id": "consent-1234567890-diia-20261028",
     "consent_given": true
   }
   ```
2. Save → **Continue**.

**Flow:** credit-history (score 720) → underwriter (band A, rate 15.9%)
→ contract-draft → verify-before-send → **PAUSE** before `offer-state`.

> **Pedagogical point (що говориш залу):** "Це справжній durable HITL —
> не sync verifier як у W2. Клієнт може підписувати consent годинами,
> workflow state збережений у Postgres, виживає `kill -9` pod'а. Resume
> коли завгодно, context не губиться."

### 1d. Resume #3 — client accepts offer (UI)

1. **Edit** → paste output для `offer-state`:
   ```json
   {"client_decision": "accept"}
   ```
2. Save → **Continue**.

**Flow:** → **PAUSE** before `sign-awaiting-state`.

### 1e. Resume #4 — contract signed via Дія (UI)

1. **Edit** → paste output для `sign-awaiting-state`:
   ```json
   {
     "disbursed": true,
     "disburse_id": "DISB-20261028-001",
     "event_key": "sha256-abc123def456",
     "was_duplicate": false
   }
   ```
2. Save → **Continue**.

→ workflow ends: `overall_status=completed`, `disbursed=true`.

**Що показуємо залу:** workflow пройшов 4 durable waits (docs, consent,
offer, sign), кожен можна паузити на години/дні, state збережений між
pod restarts. Нуль втрачених заявок.

### 1f. (Optional) Equivalent curl — для production integration

Якщо залу цікаво як це автоматизувати через webhooks замість UI
кліків:

```bash
# Start (same user_input)
curl -X POST "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_input": "Марія Коваленко, ..., Веду ФОП 8 місяців.", "stream": false}'

# Save execution_id from response → $EXEC_ID

# Resume #1 — docs
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"docs-requested-state","output":"{\"docs_uploaded\":true,\"doc_urls\":[\"s3://pb/d1.pdf\"]}"}'
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'

# Resume #2 — consent
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"consent-awaiting-state","output":"{\"consent_id\":\"consent-1234567890-diia\",\"consent_given\":true}"}'
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'

# Resume #3 — offer
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"offer-state","output":"{\"client_decision\":\"accept\"}"}'
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'

# Resume #4 — sign
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"sign-awaiting-state","output":"{\"disbursed\":true,\"disburse_id\":\"DISB-001\",\"event_key\":\"sha256-abc\",\"was_duplicate\":false}"}'
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'
```

### 1g. (Optional) kill -9 survival test

Запусти scenario 1a → 1b → до pause на `consent-awaiting-state`. Тоді:

```bash
kubectl delete pod -n codemie -l app=codemie-workflow-worker
```

Через 10с pod відновиться. У Executions tab побачиш той самий
execution, той самий paused state, той самий context. State persistence
через built-in Postgres checkpointer працює. Продовжуй з Edit+Continue
як ніби нічого не сталося.

---

## 2. Idempotent webhook replay · ~3 хв

### Setup (UI)

Повтори Scenario 1 через Edit+Continue до того, як дійшов до
`sign-awaiting-state`. Workflow PAUSED там (НЕ натискай Continue).

### First "webhook" via UI (normal)

1. **Edit** → paste output:
   ```json
   {
     "disbursed": true,
     "disburse_id": "DISB-001",
     "event_key": "sha256-abc123",
     "was_duplicate": false
   }
   ```
2. **Continue** → money disbursed. `overall_status=completed`.

### Replay same "webhook" (simulated Дія.Підпис retry)

Спроба зробити **Continue** вдруге на вже completed execution.

**Очікуване:** UI показує error ("execution not in interrupted state,
cannot resume"), або кнопки Edit/Continue disabled. Via API — `409
Conflict` або `400 Bad Request`.

**Жодного другого disburse** — ніяких 150 000 UAH × 2.

**Що говориш:** "Два рівні захисту: (1) CodeMie workflow executor
дозволяє resume ТІЛЬКИ якщо `overall_status=interrupted`. Після
`completed` — API/UI блокує повторний resume. (2) Якщо через bug
workflow би знову дійшов до sign-awaiting-state, idempotency key у
tool `check_idempotency(event_key)` повернув би cached result."

### Equivalent via curl

```bash
# First resume — success
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"sign-awaiting-state","output":"{\"disbursed\":true,\"disburse_id\":\"DISB-001\",\"event_key\":\"sha256-abc\",\"was_duplicate\":false}"}'
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'

# Replay — 409/400 Conflict
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'
```

---

## 3. Saga rollback · client cancellation · ~4 хв

### Setup (UI)

Запусти Scenario 1 через Edit+Continue до pause на `offer-state`
(contract reserved, limit заблоковано у contract-draft-state).

### Client rejects offer (UI)

1. **Edit** → paste output для `offer-state`:
   ```json
   {"client_decision": "reject"}
   ```
2. **Continue**.

**Workflow transitions:** offer-state → `compensation-state`.

**У compensation-state видно (sidebar → клік на state → Result):**
1. `limit.release(contract_id=LC-...)` → status: ok.
2. `contract.cancel(contract_id=LC-...)` → status: ok.
3. `notify.sms(tax_id=1234567890, text="Заявку скасовано, резерв знято.")`.
4. 3 audit events written.

**Що говориш:** "Saga — не магія. Це checklist compensating actions,
викликаних у reverse order. У банку кожна side-effect action (резерв,
договір, SMS) має парну rollback action. 2-Phase Commit тут не
працює — XA не існує для SMS gateway."

### Equivalent via curl
```bash
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/output" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"offer-state","output":"{\"client_decision\":\"reject\"}"}'
curl -X PUT "$CODEMIE_HOST/api/v1/workflows/$WF_ID/executions/$EXEC_ID/resume" \
  -H "Authorization: Bearer $CODEMIE_TOKEN" -d '{}'
```

---

## 4. Pre-write gate fail · verify blocks bad contract · ~3 хв

### Setup — simulate contract-draft bug via UI Edit

**Трюк для демо:** замість того щоб міняти contract-draft prompt і
rerun'ити, можна напряму **Edit** вихід `contract-draft-state` з
неправильним `rate_apr`.

1. Запусти Scenario 1 через Edit+Continue до того, як **автоматично**
   пройдуть states: docs-processing, kyc, consent, credit-history,
   underwriter (`rate_apr=18.5`).
2. Workflow дійде до `contract-draft-state` (БЕЗ interrupt_before, тому
   не паузиться автоматично — виконається з correct rate 18.5%).
3. Workflow потрапить у pause перед `verify-before-send-state`? Ні —
   verify теж без interrupt_before.

**Простіший спосіб:** додай тимчасово `interrupt_before: true` на
`contract-draft-state` у YAML → Save → rerun → paused перед
contract-draft → **Edit** → paste "bugged" output:

```json
{
  "contract_id": "LC-20261028-001",
  "contract_text_url": "s3://pb/contracts/LC-20261028-001.pdf",
  "limit_reserved": true,
  "rate_apr": 22.0
}
```

→ **Continue** → workflow робить verify-before-send:

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

**Cleanup:** прибери тимчасовий `interrupt_before: true` з
`contract-draft-state` у YAML після сценарію.

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
