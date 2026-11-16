# 01 · Durable Workflows, HITL & Saga Lab · Лабораторний практикум

## Мета для AI-архітектора

У цій практичній роботі ви розв'яжете головну інженерну проблему промислового штучного інтелекту в банках: **як перетворити короткий 10-секундний діалоговий цикл на надійний бізнес-процес, що триває 7–14 днів, витримує аварійні рестарти серверів у Kubernetes і безпечно опрацьовує відмови клієнтів**.

Ви запустите живий скінченний автомат (**LangGraph FSM**) у терміналі та протестуєте 4 критичні банківські сценарії:
1. Повний автоматичний конвеєр (**Happy Path**) із персистентним збереженням стану на кожному кроці.
2. Зупинка на людині (HITL) через `interrupt()` та відновлення за `thread_id` у НОВОМУ процесі після справжнього `kill -9` (чекпоінтер — SQLite-файл).
3. Відкат розподіленої транзакції (**Saga Pattern**) при скасуванні заявки клієнтом після резервування кредитного ліміту.
4. Захист від повторної виплати коштів (**Webhook Idempotency Barrier**) при багаторазовому надходженні повідомлення від Дія.Підпис.

---

## Запуск лабораторного стенду в терміналі

```bash
# Перейдіть у корінь проекту
cd loan-copilot

# 1. Повне демо: 4 кроки в окремих процесах (старт → kill -9 → відновлення → дубль вебхука → Saga):
.venv/bin/python -m module_3_workflows.src.workflow_engine demo

# 2. Запуск усіх автоматичних тестів надійності (очікується: 7 passed):
.venv/bin/python -m pytest module_3_workflows/tests/
```

> **Важливо:** `src/workflow_engine.py` це спрощений навчальний референс із 10 вузлів. Канонічний 12-вузловий workflow (3 durable waits, 2 умовні HITL, 1 evaluator gate) описано в deck (слайд «W3 Use-case») і реалізовано в `codemie_demo/`. Відмінності перелічено в `README.md` (розділ «Відмінності від 12-вузлового workflow»).

---

## Хід роботи: 4 інженерні експерименти

### Експеримент 1: «Автоматичний конвеєр і чекпоінти»

* **Контекст:** заявка `LC-HAPPY`, дохід 35 000 грн, сума 150 000 грн на 24 місяці, згода `consent_id` надана; скор з тестової таблиці бюро: **720** (Tier A).
* **Що відбувається:** вузли `intake → eligibility → kyc → credit_history → underwriter → contract` виконуються автоматично; `contract` реально резервує ліміт і створює договір у ledger. Далі `sign_awaiting` зупиняється через `interrupt()` і чекає подію e-sign: виплата без підпису неможлива.
* **Запуск:** `python -m module_3_workflows.src.workflow_engine start happy`, потім `status happy`.
* **Висновок:** після кожного кроку LangGraph зберігає чекпоінт у SQLite (`checkpoints.db`); стан не залежить від пам'яті процесу.

**Як це збудовано** (`workflow_engine.py`): чекпоінтер лежить у файлі, граф компілюється разом із ним, а `thread_id` ідентифікує заявку.

```python
def open_runtime(db_dir):
    saver = SqliteSaver(sqlite3.connect(str(db_dir / "checkpoints.db"), check_same_thread=False))
    ledger = Ledger(db_dir / "ledger.db")                 # «ядро банку» — теж файл
    return build_loan_workflow(saver, ledger), ledger, saver

def build_loan_workflow(checkpointer, ledger):
    wf = StateGraph(LoanWorkflowState)
    ...
    wf.add_edge("contract", "sign_awaiting")              # після договору — пауза на e-sign
    wf.add_conditional_edges("underwriter", after_underwriter)   # Tier C → hitl_committee
    return wf.compile(checkpointer=checkpointer)          # без checkpointer interrupt() не працює

app.invoke(new_application("LC-HAPPY"), config={"configurable": {"thread_id": "thread-happy"}})
```

Вузол `underwriter` рахує рішення детермінованим кодом, без LLM: `PMT`, `PTI`, потім матриця тірів.

```python
if score >= 750 and pti <= Decimal("0.35"):   tier, dec, hitl = "Tier A", "approved", False
elif score >= 650 and pti <= Decimal("0.45"): tier, dec, hitl = "Tier B", "approved", False
elif score >= 550 and pti <= Decimal("0.50"): tier, dec, hitl = "Tier C", "awaiting_hitl", True   # сіра зона
else:                                         tier, dec, hitl = "Tier D", "declined", False
```

---

### Експеримент 2: «Справжня пауза і kill -9»

* **Контекст:** заявка `LC-HITL`, скор **580** → Tier C (`SOP-CREDIT-02 §3.1`) → сіра зона.
* **Крок 1 (процес A):** `underwriter` спрямовує заявку до `hitl_committee`, вузол викликає `interrupt()`; чекпоінт збережено. Прапорець `--kill` одразу надсилає процесу `SIGKILL`: код завершення `-9`, жодного cleanup.
* **Крок 2 (процес B, інший PID):** `status hitl` показує, що граф досі чекає комітет. `resume hitl approve` виконує `Command(resume={...})`: LangGraph бере чекпоінт за `thread_id=thread-hitl`, повторно виконує лише вузол комітету і йде далі до `contract` та другої паузи (`sign_awaiting`). `intake`, `kyc` і запит до бюро **не** повторюються.
* **Безпечний дефолт:** відсутність рішення ніколи не означає схвалення; рішення `decline` завершує процес без побічних ефектів (ledger порожній).
* **Запуск:** `workflow_engine demo` (кроки 1–2) або окремими командами з `02-exercise.md`.

**Як це збудовано.** Пауза — це виклик `interrupt()` усередині вузла. Значення, яке повертає `interrupt()`, прийде з `Command(resume=...)`:

```python
def hitl_committee_node(state):
    answer = interrupt({"type": "committee_review",       # payload бачить той, хто викликав status
                        "application_id": state["application_id"], "tier": state["tier"]})
    if (answer or {}).get("decision") == "approve":       # відсутнє рішення ≠ схвалення
        return {"status": "approved", "human_decision": "approve"}
    return {"status": "declined", "human_decision": "decline"}
```

Процес A помирає без cleanup, процес B продовжує з чекпоінта:

```python
# процес A (--kill)
print(pending_interrupt(app, thread))                     # {'type': 'committee_review', ...}
os.kill(os.getpid(), signal.SIGKILL)                      # returncode == -9

# процес B (інший PID, нова пам'ять)
app, ledger, _ = open_runtime(db)                         # той самий файл checkpoints.db
app.invoke(Command(resume={"decision": "approve"}), config=thread_config("thread-hitl"))
```

Важливий нюанс: після `resume` вузол **виконується з початку**, а `interrupt()` цього разу одразу повертає відповідь. Тому до `interrupt()` у вузлі не має бути побічних ефектів (виплат, записів у ledger).

`pending_interrupt` дістає payload зі знімка стану:

```python
snap = app.get_state(thread_config(thread_id))
for task in snap.tasks:
    if task.interrupts:
        return task.interrupts[0].value
```

---

### Експеримент 3: «Saga: відкат при скасуванні»

* **Проблема:** немає 2PC між банком, підписом і карткою, тому потрібні компенсації.
* **Сценарій:** `start cancel` доходить до `sign_awaiting`: ліміт `reserved`, договір `active`. Вебхук `cancelled` приводить до `compensation_node`.
* **Компенсація (зворотний порядок):** `cancel_contract` (договір `voided`), потім `release_limit` (ліміт `released`). Виплата non-compensable і стоїть після підпису; до підпису її не було, тому відкочувати нічого зайвого.
* **Перевірка:** `status cancel` → `limit: released, contract: voided, disbursements: 0` (тест `test_saga_compensation_on_cancel`).

**Як це збудовано.** Пряма дія (`contract`) змінює ledger, компенсація робить зворотні кроки в зворотному порядку:

```python
# пряма дія
ledger.reserve_limit(app_id, amount)          # limits: reserved
ledger.create_contract(app_id, contract_id)   # contracts: active

# компенсація
def compensation_node(state):
    ledger.void_contract(app_id)              # 1) спершу договір → voided
    ledger.release_limit(app_id)              # 2) потім резерв → released
    return {"status": "cancelled", "compensation_executed": True}
```

Маршрут після події підпису вирішує, чи виплата, чи відкат:

```python
def after_sign(state):
    outcome = state.get("sign_outcome")
    if outcome == "signed":    return "disburse"
    if outcome == "cancelled": return "compensation"
    return "sign_awaiting"                    # дубль події: лишаємось в очікуванні
```

---

### Експеримент 4: «Ідемпотентний бар'єр вебхуків»

* **Аномалія:** провайдер підпису повторює вебхук через мережевий таймаут.
* **Захист рівня 1 (вхід):** `deliver_event` рахує ключ `SHA256(application_id + node + event_id)` і дивиться в таблицю `processed_events`; дубль відсікається ще до відновлення графа.
* **Захист рівня 2 (вузол):** `sign_awaiting` робить `INSERT OR IGNORE` ключа в `processed_events`; якщо ключ уже є, вузол повертає `duplicate` і повертається в очікування.
* **Захист рівня 3 (дія):** таблиця `disbursements` має `PRIMARY KEY (application_id)`: навіть повторне виконання `disburse` не створить другої виплати.
* **Запуск:** `demo` (крок 3: той самий вебхук тричі, кожен раз новий процес) → `disbursements: 1`. Тести: `test_duplicate_webhook_is_idempotent`, `test_disburse_is_idempotent_even_without_ingress_check`.

**Як це збудовано.** Ключ детермінований: той самий `event_id` завжди дає той самий ключ.

```python
def event_key(application_id, node, event_id):
    return hashlib.sha256(f"{application_id}:{node}:{event_id}".encode()).hexdigest()[:16]
```

Рівень 1, на вході: read-only перевірка до відновлення графа.

```python
def deliver_event(app, ledger, application_id, thread_id, event):
    key = event_key(application_id, "sign_awaiting", str(event.get("event_id", "")))
    if ledger.seen_event(key):                # SELECT 1 FROM processed_events WHERE key=?
        return {"duplicate": True}            # граф навіть не будимо
    return app.invoke(Command(resume=event), config=thread_config(thread_id))
```

Рівень 2, у вузлі: атомарне «захоплення» ключа. Перемагає лише один із двох одночасних запитів.

```python
def claim_event(self, key):
    cur = self.conn.execute("INSERT OR IGNORE INTO processed_events VALUES(?,?)", (key, time.time()))
    return cur.rowcount == 1                  # 1 → ми перші; 0 → хтось уже обробив
```

Рівень 3, у даних: навіть якщо рівні 1 і 2 обійшли, база не дасть другої виплати.

```python
# CREATE TABLE disbursements(application_id TEXT PRIMARY KEY, amount REAL, ts REAL)
def disburse(self, app_id, amount):
    try:
        self.conn.execute("INSERT INTO disbursements VALUES(?,?,?)", (app_id, amount, time.time()))
        return True
    except sqlite3.IntegrityError:            # друга спроба = порушення PRIMARY KEY
        return False
```

Чому два рівні перед БД: `seen_event` дешево відсікає більшість дублів, `claim_event` закриває гонку між двома паралельними запитами, а `PRIMARY KEY` залишається останнім бар'єром на випадок помилки в коді вище.

---

## Що ви забираєте з лабораторії

1. **Референсний FSM на LangGraph:** код `workflow_engine.py` з типізованим станом, SQLite-чекпоінтером і паузами через `interrupt()` (спрощення відносно 12-вузлового workflow з deck; «ядро банку» — стаб у SQLite).
2. **Шаблон HITL:** `interrupt()` + `Command(resume=...)`: пауза переживає смерть процесу і продовжується з того самого вузла.
3. **Паттерн Saga:** перевірений механізм компенсуючих транзакцій для відкату розподілених банківських операцій.
