# 01 · Durable Workflows, HITL & Saga Lab · Лабораторний практикум

## Мета для AI-архітектора

У цій практичній роботі ви розв'яжете головну інженерну проблему промислового штучного інтелекту в банках: **як перетворити короткий 10-секундний діалоговий цикл на надійний бізнес-процес, що триває 7–14 днів, витримує аварійні рестарти серверів у Kubernetes і безпечно опрацьовує відмови клієнтів**.

Ви проведете **одну заявку** через весь життєвий цикл на живому скінченному автоматі (**LangGraph FSM**) і перевірите 4 критичні банківські поведінки. Експерименти йдуть **по черзі, на одній заявці `LC-HITL` і одній базі**: результат кожного кроку є вхідним станом для наступного.

| № | Що перевіряємо | Заявка | Стан після експерименту |
|---|----------------|--------|-------------------------|
| 1 | Конвеєр, чекпоінт і `kill -9` | `LC-HITL` | граф на паузі «комітет», процес мертвий |
| 2 | Відновлення в новому процесі (HITL) | `LC-HITL` | комітет схвалив, ліміт зарезервовано, граф чекає e-sign |
| 3 | Ідемпотентний бар'єр вебхуків | `LC-HITL` | три однакові вебхуки → 1 виплата |
| 4 | Saga: відкат при скасуванні | `LC-CANCEL` | та сама точка очікування e-sign, але клієнт скасував → компенсація |

---

## Запуск

Усі команди виконуйте з кореня проєкту (`loan-copilot`) у активованому віртуальному середовищі (`pip install -r requirements.txt`).

```bash
# Швидко: усі 4 експерименти одним прогоном (окремі процеси, тимчасова БД)
python -m module_3_workflows.src.workflow_engine demo

# Покроково (нижче): кожна команда — НОВИЙ процес, стан у файлах module_3_workflows/data/runtime/
rm -rf module_3_workflows/data/runtime      # чистий старт перед експериментом 1

# Автоматичні тести надійності (очікується: 7 passed)
python -m pytest module_3_workflows/tests/ -v
```

> **Важливо:** `src/workflow_engine.py` це спрощений навчальний референс із 10 вузлів. Канонічний 12-вузловий workflow (3 durable waits, 2 умовні HITL, 1 evaluator gate) описано в deck (слайд «W3 Use-case») і реалізовано в `codemie_demo/`. Відмінності перелічено в `README.md`.

---

## Хід роботи: 4 послідовні експерименти

### Експеримент 1: «Конвеєр, чекпоінт і kill -9»

* **Контекст:** заявка `LC-HITL`, сума 150 000 грн на 24 місяці, згода на запит до бюро надана; скор із тестової таблиці бюро: **580** → Tier C (`SOP-CREDIT-02 §3.1`) → сіра зона.
* **Що відбувається:** вузли `intake → eligibility → kyc → credit_history → underwriter` виконуються автоматично; `underwriter` спрямовує заявку до `hitl_committee`, вузол викликає `interrupt()`, чекпоінт зберігається в SQLite. Прапорець `--kill` одразу надсилає процесу `SIGKILL`: жодного cleanup.

```bash
python -m module_3_workflows.src.workflow_engine start hitl --kill
```

* **Очікуєте у виводі:** п'ять рядків `▶ …` (вузли), `⏸ committee: … (interrupt)`, `pid=… чекпоінт збережено, граф чекає: {'type': 'committee_review', …}` і рядок про `kill -9`. Термінал повертається без помилки Python: процес просто зник.
* **Висновок:** стан заявки не залежить від пам'яті процесу, він лежить у `module_3_workflows/data/runtime/checkpoints.db`.
* **Перевірка історії** (нова команда, процес мертвий, а історія є):

```bash
python -m module_3_workflows.src.workflow_engine status hitl
```

  Таблиця «Історія статусів» має 6 рядків: `init → intake → eligibility → kyc → credit_history → underwriter`; останній рядок `awaiting_hitl ◀ зараз`, далі `hitl_committee`.

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

app.invoke(new_application("LC-HITL"), config={"configurable": {"thread_id": "thread-hitl"}})
```

Вузол `underwriter` рахує рішення детермінованим кодом, без LLM: `PMT`, `PTI`, потім матриця тірів.

```python
if score >= 750 and pti <= Decimal("0.35"):   tier, dec, hitl = "Tier A", "approved", False
elif score >= 650 and pti <= Decimal("0.45"): tier, dec, hitl = "Tier B", "approved", False
elif score >= 550 and pti <= Decimal("0.50"): tier, dec, hitl = "Tier C", "awaiting_hitl", True   # сіра зона
else:                                         tier, dec, hitl = "Tier D", "declined", False
```

Пауза — це виклик `interrupt()` усередині вузла комітету, а `kill -9` у `--kill` це просто:

```python
print(pending_interrupt(app, thread))      # {'type': 'committee_review', ...}
os.kill(os.getpid(), signal.SIGKILL)       # returncode == -9
```

---

### Експеримент 2: «Відновлення в новому процесі»

* **Контекст:** процес із експерименту 1 мертвий, `LC-HITL` досі чекає комітет.
* **Крок 1:** перевірте, що пауза пережила смерть процесу.

```bash
python -m module_3_workflows.src.workflow_engine status hitl
```

  Очікуєте: `waiting_for: {'type': 'committee_review', …}`, `ledger: {'limit': None, 'contract': None, 'disbursements': 0}` (до рішення комітету грошей не зачеплено).

* **Крок 2:** людина (комітет) схвалює.

```bash
python -m module_3_workflows.src.workflow_engine resume hitl approve --comment "наявний строковий депозит"
```

  Очікуєте: інший `pid`, той самий payload паузи, далі `▶ contract: Договір і резерв ліміту 150,000.00 грн` і друга пауза `⏸ sign_awaiting`. Рядків `intake`, `kyc` чи запиту до бюро **немає**: вони не повторюються.
* **Перевірка:** `status hitl` → до таблиці додалися два рядки: `hitl_committee · approved` і `contract · approved ◀ зараз` (далі `sign_awaiting`). Ledger: `{'limit': 'reserved', 'contract': 'active', 'disbursements': 0}`, `waiting_for: {'type': 'await_sign_event', …}`. Перші 6 рядків не змінилися: це доказ, що `intake`/`kyc` не виконувались вдруге.
* **Безпечний дефолт:** відсутність рішення ніколи не означає схвалення; рішення `decline` завершує процес без побічних ефектів (ledger порожній).

**Як це збудовано.** Вузол комітету викликає `interrupt()`, а значення, яке він поверне, прийде з `Command(resume=...)`:

```python
def hitl_committee_node(state):
    answer = interrupt({"type": "committee_review",       # payload бачить той, хто викликав status
                        "application_id": state["application_id"], "tier": state["tier"]})
    if (answer or {}).get("decision") == "approve":       # відсутнє рішення ≠ схвалення
        return {"status": "approved", "human_decision": "approve"}
    return {"status": "declined", "human_decision": "decline"}
```

Новий процес відкриває той самий файл і продовжує:

```python
app, ledger, _ = open_runtime(db)                         # той самий checkpoints.db
app.invoke(Command(resume={"decision": "approve"}), config=thread_config("thread-hitl"))
```

Важливий нюанс: після `resume` вузол **виконується з початку**, а `interrupt()` цього разу одразу повертає відповідь. Тому до `interrupt()` у вузлі не має бути побічних ефектів (виплат, записів у ledger).

`pending_interrupt` (те, що показує `status`) дістає payload зі знімка стану:

```python
snap = app.get_state(thread_config(thread_id))
for task in snap.tasks:
    if task.interrupts:
        return task.interrupts[0].value
```

---

### Експеримент 3: «Ідемпотентний бар'єр вебхуків»

* **Контекст:** `LC-HITL` чекає подію e-sign (стан після експерименту 2). Провайдер підпису повторює вебхук через мережевий таймаут: той самий `event_id` приходить тричі.

```bash
python -m module_3_workflows.src.workflow_engine event hitl signed --event-id dia-evt-1
python -m module_3_workflows.src.workflow_engine event hitl signed --event-id dia-evt-1
python -m module_3_workflows.src.workflow_engine event hitl signed --event-id dia-evt-1
```

* **Очікуєте:** перший виклик: `✔ disburse: ВИПЛАТУ виконано: 150,000.00 грн`, `Ledger: {'limit': 'reserved', 'contract': 'active', 'disbursements': 1}`. Другий і третій: `○ webhook dia-evt-1: дубль, ігноруємо (ключ …)` і `disbursements` усе ще **1**.
* **Перевірка історії:** `status hitl` → після першого вебхука додалися рядки `sign_awaiting` і `disburse · disbursed ◀ зараз`. Другий і третій вебхуки **не додали жодного рядка**: граф навіть не відновлювався.
* **Захист рівня 1 (вхід):** `deliver_event` рахує ключ `SHA256(application_id + node + event_id)` і дивиться в `processed_events`; дубль відсікається ще до відновлення графа.
* **Захист рівня 2 (вузол):** `sign_awaiting` робить `INSERT OR IGNORE` ключа; якщо ключ уже є, повертає `duplicate` і лишається в очікуванні.
* **Захист рівня 3 (дія):** таблиця `disbursements` має `PRIMARY KEY (application_id)`: навіть повторне виконання `disburse` не створить другої виплати.
* **Тести:** `test_duplicate_webhook_is_idempotent`, `test_disburse_is_idempotent_even_without_ingress_check`.

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

### Експеримент 4: «Saga: відкат при скасуванні»

* **Проблема:** немає 2PC між банком, підписом і карткою, тому потрібні компенсації. Експеримент 3 показав гілку «клієнт підписав»; тепер друга гілка тієї самої точки очікування e-sign: «клієнт скасував». Для цього беремо другу заявку, `LC-CANCEL` (скор 720, Tier A, без комітету).

```bash
python -m module_3_workflows.src.workflow_engine start cancel
python -m module_3_workflows.src.workflow_engine status cancel         # limit: reserved, contract: active
python -m module_3_workflows.src.workflow_engine event cancel cancelled --event-id c1
python -m module_3_workflows.src.workflow_engine status cancel
```

* **Очікуєте:** після `start`: ліміт `reserved`, договір `active`, граф чекає e-sign. Після події `cancelled`: `↺ saga: Компенсація: cancel_contract → release_limit` і `limit: released, contract: voided, disbursements: 0` (тест `test_saga_compensation_on_cancel`). В історії `status cancel` останні рядки: `sign_awaiting`, `compensation · cancelled ◀ зараз`, далі кінець.
* **Компенсація (зворотний порядок):** `cancel_contract`, потім `release_limit`. Виплата non-compensable і стоїть після підпису; до підпису її не було, тому відкочувати нічого зайвого.
* **Підсумок усієї лаби:** `LC-HITL` (виплата 1, ліміт `reserved`) і `LC-CANCEL` (виплат 0, ліміт `released`) в одній базі — два шляхи від однієї точки очікування.

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

## Що ви забираєте з лабораторії

1. **Референсний FSM на LangGraph:** код `workflow_engine.py` з типізованим станом, SQLite-чекпоінтером і паузами через `interrupt()` (спрощення відносно 12-вузлового workflow з deck; «ядро банку» — стаб у SQLite).
2. **Шаблон HITL:** `interrupt()` + `Command(resume=...)`: пауза переживає смерть процесу і продовжується з того самого вузла.
3. **Ідемпотентність у три рівні:** ключ події на вході, `claim` у вузлі, `PRIMARY KEY` у даних.
4. **Паттерн Saga:** перевірений механізм компенсуючих транзакцій для відкату розподілених банківських операцій.
