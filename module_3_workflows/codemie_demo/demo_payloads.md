# W3 demo · Happy path + Rainy day

Workflow `loan-copilot-durable-w3-embedded` має **6 HITL state'ів з
префіксом `webhook_*`** — кожен симулює external service dependency
(Privat24, Дія.Підпис, credit committee, SMS gateway). У production
вони resume'яться через real webhook / message queue event. У demo —
через UI **Edit + Continue** (той самий effect як webhook payload).

## ⚠ CodeMie UI quirk про `interrupt_before`

**Факт:** workflow паузиться НА `webhook_*` state (саме його output
встановлює Edit).

**Але UI показує:** попередній completed state highlighted з Interrupted
badge. Наступний (paused) `webhook_*` state ще не з'являється у sidebar —
він там з'явиться тільки ПІСЛЯ Edit+Continue.

**Як орієнтуватися:**
- Sidebar highlight = **last completed** state (misleading).
- Центральна панель **Input prompt** = показує що буде виконано далі.
  Якщо там текст на кшталт *"Pause workflow until client provides
  explicit UBKI consent..."* — ти знаєш, що paused state це
  `webhook_consent_granted`.
- Edit → paste output у форматі `webhook_consent_granted`'s output_schema.
- Continue → workflow застосовує твій output + продовжує.

**Чому так:** CodeMie/LangGraph `interrupt_before` = "pause BEFORE
state runs". State ще не виконувався, тому його немає у completed
sidebar. Коли Edit+Continue — state logically "runs" з output з Edit,
потім з'являється у sidebar.

**Start input (той самий для обох сценаріїв):**

```
Марія Коваленко, 1234567890, +380501234567, maria@example.com,
дохід 35000, сума 80000, строк 24 міс, consumer.
Веду ФОП 8 місяців.
```

---

## Happy path (approved + disbursed) — 4 webhook resumes

1. **Run workflow** → paste start input → Submit.
2. Pause на `webhook_docs_uploaded`. Edit → Continue:
   ```json
   {"docs_uploaded": true, "doc_urls": ["s3://pb/d1.pdf", "s3://pb/d2.pdf"]}
   ```
3. Pause на `webhook_consent_granted`. Edit → Continue:
   ```json
   {"consent_id": "consent-1234567890-diia", "consent_given": true}
   ```
4. Pause на `webhook_offer_decision`. Edit → Continue:
   ```json
   {"client_decision": "accept"}
   ```
5. Pause на `webhook_contract_signed`. Edit → Continue:
   ```json
   {"disbursed": true, "disburse_id": "DISB-001", "event_key": "sha256-abc", "was_duplicate": false}
   ```
6. ✅ `overall_status=completed, disbursed=true`.

---

## Rainy day (Saga rollback) — client rejects offer

1. **Run workflow** → paste start input → Submit.
2. Pause на `webhook_docs_uploaded`. Edit → Continue:
   ```json
   {"docs_uploaded": true, "doc_urls": ["s3://pb/d1.pdf"]}
   ```
3. Pause на `webhook_consent_granted`. Edit → Continue:
   ```json
   {"consent_id": "consent-1234567890-diia", "consent_given": true}
   ```
4. Pause на `webhook_offer_decision`. Edit → Continue з `reject`:
   ```json
   {"client_decision": "reject"}
   ```
5. Workflow transitions → `compensation-state` runs Saga:
   - `limit.release(contract_id)` ✅
   - `contract.cancel(contract_id)` ✅
   - `notify.sms` ✅
6. ✅ `overall_status=completed` (via compensation).

---

## Optional detours

**OCR failure branch** — додай `OCR_FAIL_DEMO` у start input → після
docs upload буде додатковий pause на `webhook_analyst_decision`:
```json
{"analyst_approved": true, "analyst_notes": "manual OCR verified"}
```

**Committee review branch** — зміни start input на client з low credit
score (`tax_id=9876543210`, `сума=100000`) → після underwriter буде
додатковий pause на `webhook_committee_decision`:
```json
{"committee_decision": "approve", "committee_notes": "approved with exception"}
```

**kill -9 survival** — під час будь-якого pause: `kubectl delete pod
-n codemie -l app=codemie-workflow-worker`. Pod відновлюється, той
самий paused state чекає. Continue нормально.

---

## API equivalent (production integration)

Замість UI Edit + Continue → `PUT /output` + `PUT /resume`:

```bash
curl -X PUT "$HOST/api/v1/workflows/$WF/executions/$EXEC/output" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"state_id":"webhook_docs_uploaded","output":"{\"docs_uploaded\":true,\"doc_urls\":[\"s3://pb/d1.pdf\"]}"}'

curl -X PUT "$HOST/api/v1/workflows/$WF/executions/$EXEC/resume" \
  -H "Authorization: Bearer $TOKEN" -d '{}'
```
