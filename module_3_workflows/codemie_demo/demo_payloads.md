# W3 demo · Happy path + Rainy day

Workflow `loan-copilot-durable-w3-embedded` emulates 6 external service
dependencies via **send_*** → **webhook_*** state pairs:

| Trigger (send_*) | Durable wait (webhook_*) | External service |
|---|---|---|
| send_docs_request | webhook_docs_uploaded | Privat24 upload |
| escalate_to_analyst | webhook_analyst_decision | Operator queue |
| send_consent_request | webhook_consent_granted | Дія.Підпис consent |
| escalate_to_committee | webhook_committee_decision | Credit committee |
| send_offer | webhook_offer_decision | Client push |
| send_contract_sign_request | webhook_contract_signed | Дія.Підпис sign |

**Sidebar UX:** `send_*` state completes → sidebar shows it with ✅ +
Interrupted overall badge → reads as "notification sent, waiting for
response". Edit вводить output для **webhook_*** paused state (CodeMie
UI quirk — див. нижче).

**Start input (той самий для обох сценаріїв):**

```
Марія Коваленко, 1234567890, +380501234567, maria@example.com,
дохід 35000, сума 80000, строк 24 міс, consumer.
Веду ФОП 8 місяців.
```

---

## Happy path (approved + disbursed) — 4 webhook resumes

1. **Run workflow** → paste start input → Submit.
2. Sidebar: `send_docs_request ✅` → PAUSE on `webhook_docs_uploaded`. Edit → Continue:
   ```json
   {"docs_uploaded": true, "doc_urls": ["s3://pb/d1.pdf", "s3://pb/d2.pdf"]}
   ```
3. Sidebar: `send_consent_request ✅` → PAUSE on `webhook_consent_granted`. Edit → Continue:
   ```json
   {"consent_id": "consent-1234567890-diia", "consent_given": true}
   ```
4. Sidebar: `send_offer ✅` → PAUSE on `webhook_offer_decision`. Edit → Continue:
   ```json
   {"client_decision": "accept"}
   ```
5. Sidebar: `send_contract_sign_request ✅` → PAUSE on `webhook_contract_signed`. Edit → Continue:
   ```json
   {"disbursed": true, "disburse_id": "DISB-001", "event_key": "sha256-abc", "was_duplicate": false}
   ```
6. ✅ `overall_status=completed, disbursed=true`.

---

## Rainy day (Saga rollback) — client rejects offer

1. **Run workflow** → paste start input → Submit.
2. PAUSE on `webhook_docs_uploaded`. Edit → Continue:
   ```json
   {"docs_uploaded": true, "doc_urls": ["s3://pb/d1.pdf"]}
   ```
3. PAUSE on `webhook_consent_granted`. Edit → Continue:
   ```json
   {"consent_id": "consent-1234567890-diia", "consent_given": true}
   ```
4. PAUSE on `webhook_offer_decision`. Edit → Continue з **reject**:
   ```json
   {"client_decision": "reject"}
   ```
5. Workflow auto-transitions → `compensation-state` executes Saga:
   - `limit.release(contract_id)` ✅
   - `contract.cancel(contract_id)` ✅
   - `notify.sms` ✅
6. ✅ `overall_status=completed` (via compensation).

---

## Optional detours

**OCR failure branch** — додай `OCR_FAIL_DEMO` у start input →
додатковий `escalate_to_analyst ✅` → PAUSE on `webhook_analyst_decision`:
```json
{"analyst_approved": true, "analyst_notes": "manual OCR verified"}
```

**Committee review branch** — client з low credit score
(`tax_id=9876543210, сума=100000`) → `escalate_to_committee ✅` → PAUSE
on `webhook_committee_decision`:
```json
{"committee_decision": "approve", "committee_notes": "approved with exception"}
```

**kill -9 survival** — під час будь-якого pause: `kubectl delete pod
-n codemie -l app=codemie-workflow-worker`. Pod відновлюється, той
самий paused state чекає. Continue нормально.

---

## ⚠ CodeMie UI quirk про `interrupt_before`

Edit встановлює output для **paused webhook_*** state (verified у LangGraph
source), а не для попереднього completed `send_*`. Sidebar показує
`send_*` як last completed з overall Interrupted badge — це visual
pointer, не місце де сидить execution.

Якщо не впевнений який state paused — дивися центральний Input prompt
panel, там видно який state наступним стартує.

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
