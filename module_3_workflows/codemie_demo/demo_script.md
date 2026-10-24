# W3 live demo script · ~15 хв у CodeMie Workflows

> Головний message: "Loan Copilot stops being a demo. Workflow виживає
> `kill -9` K8s о 3 ночі, не дублює виплат, виконує Saga rollback."

## Хронометраж

| Хв | Що показуємо | Вкладка |
|---|---|---|
| 0:00–1:00 | Чому Sequential workflow, не chat | Workflows list |
| 1:00–3:00 | YAML walkthrough (11 states, 3 waits, 2 HITL, 1 evaluator, 1 compensation) | Workflow edit |
| 3:00–8:00 | **Scenario 1** · survive `kill -9` + 3 resumes | Executions + terminal |
| 8:00–11:00 | **Scenario 3** · Saga rollback на client reject | Executions |
| 11:00–13:00 | **Scenario 4** · Pre-write gate blocks bad contract | Executions + Langfuse |
| 13:00–15:00 | Q&A + міграційний шлях W2→W3 | Discussion |

---

## 0:00–1:00 · Чому Sequential workflow, не chat

**Що говориш:**
> "W1-W2 ми робили parent + sub-assistants через chat. Для grounding +
> tools це працювало. Для durable waits — ні. CodeMie chat не паузиться
> на 24 години, не підхоплюється webhook'ом, і не виживає restart pod'ів.
>
> Сьогодні переходимо на Sequential workflow — це той самий LangGraph
> FSM під капотом, але з Postgres checkpointer. CodeMie Workflow Engine
> = CodeMie-tenant replacement для self-hosted LangGraph."

**Клік:** Admin → Workflows → scroll — покажи, що у tenant є інші
production workflows (deploy pipelines, test generators), не тільки
наш loan.

---

## 1:00–3:00 · YAML walkthrough

**Клік:** `loan-copilot-durable-w3` → Edit → YAML view.

**Scroll через states** та підкресли ключові point'и:

1. **State 2 `docs-requested-state`** — `interrupt_before: true`.
   > "Durable wait. Workflow паузиться у БД (Postgres checkpointer).
   > Нуль CPU, нуль memory. 48 годин — не проблема."

2. **State 4 `analyst-review-state`** — conditional HITL.
   > "Активується ТІЛЬКИ коли OCR confidence < 0.85. Happy path цей
   > node обходить. Якщо фото чітке — zero human cost."

3. **State 7 `underwriter-state`** — `switch` з 3 cases.
   > "Deterministic routing: approve → contract-draft, committee_review →
   > committee gate, decline → end. Жодного LLM на routing — safer для audit."

4. **State 10 `verify-before-send-state`** — Pre-write gate.
   > "Остання лінія захисту ПЕРЕД підписом. Temperature 0, порівнює числа.
   > Якщо contract text каже 22% а underwriter 18.5% — БЛОК."

5. **State X `compensation-state`** — Saga.
   > "Викликається з 4 кроків: docs timeout, analyst reject, verify fail,
   > offer reject/expire. Усі ведуть сюди — DRY. Один state робить
   > rollback: limit.release + contract.cancel + notify."

---

## 3:00–8:00 · Scenario 1 · survive kill -9

### 3:00–4:00 · Start execution

**Клік:** Workflows → `loan-copilot-durable-w3` → Start Execution.

**Payload:**
```json
{"message": "Марія Коваленко, 1234567890, +380501234567, maria@..., 35000, 150000, 24, consumer.", "consent_id": "consent-demo-01"}
```

**У Executions tab видно:**
- Status: `running`.
- Current state: `intake-state` → `docs-requested-state`.
- Status change: `interrupted` (на docs-requested wait).

### 4:00–5:00 · kill -9 the pod

**Відкрий terminal у k9s:**
```bash
kubectl get pods -n codemie | grep workflow-worker
kubectl delete pod codemie-workflow-worker-xxx -n codemie
```

**Що говориш:**
> "Це реальний prod incident: autoscaler масштабує down вночі, SIGKILL
> виконавчим подам. Якщо наш workflow — у Python asyncio loop у памʼяті
> поду, усе втрачено. Клієнт о 7 ранку бачить, що заявка зникла."

**Через 10 секунд:** pod re-created, worker reconnects.

**Клік назад у Executions tab:** статус workflow все ще `interrupted:docs-requested-state`. **State lives у Postgres.**

### 5:00–6:00 · Resume docs.uploaded

**Terminal:**
```bash
curl -X POST $CODEMIE/api/v1/workflows/<wf>/executions/<thread_id>/resume \
  -H 'Content-Type: application/json' \
  -d '{"docs_uploaded": true, "doc_urls": ["s3://pb/intake/passport.jpg", "s3://pb/intake/income.pdf"]}'
```

**Executions tab:** workflow transitions:
- docs-requested → docs-processing (OCR 0.92) → kyc → credit-history → underwriter (band A) → contract-draft → verify (pass) → **offer-state PAUSE**.

### 6:00–7:00 · Resume offer.decision

```bash
curl -X POST .../resume \
  -d '{"client_decision": "accept"}'
```

→ **sign-awaiting-state PAUSE**.

### 7:00–8:00 · Resume contract.signed + show idempotency

**First resume:**
```bash
curl -X POST .../resume \
  -d '{"disbursed": true, "disburse_id": "DISB-001", "event_key": "sha256-abc", "was_duplicate": false}'
```

→ workflow completes. `disbursed=true`.

**Second resume (replay):**
```bash
curl -X POST .../resume \
  -d '{"disbursed": true, "disburse_id": "DISB-001", "event_key": "sha256-abc"}'
```

→ **CodeMie повертає 409 Conflict** (execution already completed).
→ **Другого disburse не було.**

**Що говориш:**
> "Два рівні захисту: (1) CodeMie execution state не дозволяє resume
> завершеного workflow. (2) У production ще й tool-level: Python wrapper
> `disburse` перевіряє composite key у БД перед money movement.
>
> 150 000 UAH виплачені рівно один раз. При 14k заявках/день — це
> різниця між банком і судом з НБУ."

---

## 8:00–11:00 · Scenario 3 · Saga rollback

### Setup

Запусти Scenario 1 знову до offer-state.

### Client rejects

```bash
curl -X POST .../resume \
  -d '{"client_decision": "reject"}'
```

**Executions tab:**
- offer-state → compensation-state.

**У compensation-state logs:**
```
[compensation-agent] tool.limit.release(contract_id=LC-...) → ok
[compensation-agent] tool.contract.cancel(contract_id=LC-...) → ok
[compensation-agent] tool.notify.send(template=offer_rejected, channel=sms+email) → ok
[audit] events written: limit_released_rollback, contract_cancelled_rollback, client_notified
```

**Що говориш:**
> "150k UAH резерву знято. Договір анульовано. Клієнту SMS. Усе за ~2
> секунди. У легасі flow це людина вручну знімала резерв з Excel'ю —
> 30 хвилин роботи операційника на кожну відмову.
>
> 2PC тут не працює — у нас 3 різні бек-енди (ліміт, document system,
> SMS gateway). Saga з компенсуючими діями — промисловий pattern."

---

## 11:00–13:00 · Scenario 4 · Pre-write gate fail

### Setup

**Штучний bug:** у `contract.draft` tool mock поверне `rate_apr=22.0`
замість `18.5` для `tax_id=1234567890`.

```bash
# Патч mock у Tools → contract.draft → Edit mock_responses:
"1234567890": {rate_apr: 22.0, contract_id: "LC-..."}  # bug introduced
```

### Run

Той самий payload #1.

### Що видно

Pass intake → eligibility → ... → underwriter (`rate_apr=18.5`) →
contract-draft (**поверне 22.0, bug у template**) → **verify-before-send
FAIL**:

```json
{
  "verify_passed": false,
  "discrepancies": ["rate_mismatch: contract=22.0 vs underwriter=18.5"],
  "escalation_level": "P0"
}
```

→ compensation-state (limit.release + contract.cancel + notify).

### Langfuse trace

Відкрий Langfuse → знайти цю execution → подивитися span tree:
- verify-before-send-state: 0.0s latency, тільки читання + compare → FAIL.
- compensation-state: 3 tool calls.
- Audit events: `rate_mismatch_detected` → engineering P0 ticket.

**Що говориш:**
> "Це saved an Air Canada case. Underwriter calculated 18.5%, Jinja template
> had a bug and rendered 22% у договорі. Якби не pre-write gate — клієнт
> підписав би договір з розбіжністю між ставкою, яку йому сказали, та
> ставкою у документі.
>
> Трибунал Канади оштрафував Air Canada на подібному — bot committed
> компанію на політику, яку компанія не має. У нас — deterministic gate
> ловить mismatch ДО того, як контент потрапив до клієнта.
>
> Engineering fix bug у template, в клієнта — SMS 'технічна затримка'.
> Зеро репутаційних втрат."

---

## 13:00–15:00 · Q&A

**Очікувані питання + відповіді:**

**Q: Чому Postgres checkpointer, а не Redis?**
A: Postgres — ACID, SQL queries для audit (НБУ любить SQL),
reliability. Redis — fast cache, не для WAL-grade персистенції.
CodeMie підтримує обидва, але для банк workflows → Postgres.

**Q: Що якщо webhook від Дія.Підпис не приходить (їх прод впав)?**
A: `max_wait_duration: 604800` (7 днів) на `sign-awaiting-state`.
Після 7d → auto-transition у `compensation-state`. Клієнт отримує
SMS "термін оферу закінчився, оформіть новий", limit звільняється.

**Q: Чому `verify-before-send` окремий state, а не частина
`contract-draft`?**
A: Separation of concerns. Contract-draft ЗНАЄ як генерувати договір
(може бути створений front-end team). Verify ЗНАЄ як перевіряти
математичну консистентність (створений risk/compliance team). Різні
owners, різні тести, різні SLO. Що важливіше — verify має бути
deterministic (не LLM-reasoned).

**Q: Scaleability — 14k заявок/день, скільки pod'ів треба?**
A: Durable waits не займають pod resources (state у БД). Only
active LLM calls — ~2-3 pods для 14k/day peak. Пояснимо у W4 FinOps.

---

## Переходи у W4

> "Домашнє: `module_3_workflows/02-exercise.md` — найважча лаба курсу,
> але якщо застрягли на LangGraph API — переключитись на псевдокод, це
> все одно зарахується.
>
> Наступний тиждень — W4 — вже готовий production workflow. Але він не
> виміряний: ми не знаємо, чи він галюцинує. Ми не знаємо, скільки
> коштує. Ми не знаємо, чи стійкий до prompt injection. W4 про те,
> як це все виміряти, задокументувати, та захистити бюджет перед CFO."
