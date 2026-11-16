# 02 · Exercise · ваша W3 **домашня** лаба — Durable Workflows, HITL та Saga-відкати

> ⏱ **~120–150 хв · самостійно після вебінару** · це найважча лаба курсу · на занятті показуємо implementation у CodeMie workflows
> 🤖 **Vibe-code OK** — але починайте з graph skeleton; Saga-відкат і idempotency barrier спробуйте написати власноруч, навіть якщо повільніше (це тема, що найчастіше з'являється на співбесідах на SA-роль)
> 💡 Якщо застрягли на LangGraph API — переключитись на псевдокод і описати state transitions, це все одно зарахується

## Learning objective

На практиці побудувати відмовостійкий бізнес-процес на базі **LangGraph FSM**:
1. Перетворити лінійний агентний скрипт на **скінченний автомат із персистентними чекпоінтами**.
2. Реалізувати асинхронне очікування клієнта (**Durable Wait**) без використання `asyncio.sleep`, що витримує перезапуск серверів у Kubernetes.
3. Впровадити умовний людський нагляд (**Conditional HITL**) для заявок у сірій зоні ризику (Tier C).
4. Захистити фінансові транзакції за допомогою розподіленого відкату (**Saga Pattern**) та ідемпотентного бар'єру; **Pre-write Evaluator Gate** (`verify_before_send`) показано в `codemie_demo/` (у Python-референсі його немає).

---

## Що покажемо

Два сценарії, які відтворюються в коді:
1. **Перезапуск процесу посеред заявки:** заявка доходить до комітету, процес отримує справжній `SIGKILL`, окремий новий процес підхоплює стан із SQLite за `thread_id` і продовжує з місця зупинки.
2. **Скасування клієнтом після резерву ліміту:** клієнту зарезервовано ліміт 150 000 грн; при скасуванні заявки Saga-компенсація знімає резерв і анулює договір.

---

## Завдання

### 1. Розберіть граф станів у `src/workflow_engine.py`

Готовий граф запускається як є; ваше завдання — прочитати код і пояснити його (потім зробіть розширення з кроку 6).
* Стан типізовано через `LoanWorkflowState` (TypedDict).
* Вузли: `intake` → `eligibility` → `kyc` → `credit_history` → `underwriter`, далі розгалуження:
  * Tier C (скор 550–650) → `hitl_committee` (пауза через `interrupt()`).
  * Tier A/B → `contract` (резерв ліміту й договір у ledger) → `sign_awaiting` (друга пауза: чекає вебхук підпису).
  * Tier D → `END`.
* `sign_awaiting` після події: `signed` → `disburse`; `cancelled` → `compensation` (Saga); дубль події → знову чекає.

### 2. Переконайтеся, що пауза справжня: persistent чекпоінтер + `kill -9`

Чекпоінтер — SQLite-файл (`SqliteSaver`), а не пам'ять процесу. Пауза — це `interrupt()`; граф відновлюється командою `Command(resume=...)` з **того самого вузла**, який чекав, а не з `intake`.

```bash
python -m module_3_workflows.src.workflow_engine demo
```
У виводі знайдіть: код завершення процесу A дорівнює `-9`; процес B має інший PID, бачить ту саму паузу та продовжує. Пояснювальні питання: чому процес B не повторює `intake`/`kyc`? Що саме лежить у `checkpoints.db`?

Окремі кроки (кожен — новий процес; починайте з чистої бази: `rm -rf module_3_workflows/data/runtime`). Кроки 2–4 далі йдуть послідовно на одній базі:
```bash
python -m module_3_workflows.src.workflow_engine start hitl --kill      # дійти до комітету і вбити себе
python -m module_3_workflows.src.workflow_engine status hitl            # що чекає граф
python -m module_3_workflows.src.workflow_engine resume hitl approve    # людина схвалює
```
Безпечний дефолт HITL: відсутність рішення ніколи не означає схвалення (у референсі немає SLA-таймера; у продакшні — hold/ескалація або decline за політикою).

### 3. Ідемпотентний бар'єр (продовжує крок 2: `LC-HITL` чекає e-sign)

Ключ події: `SHA256(application_id + node + event_id)`, таблиця `processed_events` з `PRIMARY KEY`. Другий захист: таблиця `disbursements` з `PRIMARY KEY (application_id)` — навіть якщо дубль прорветься, подвійна виплата неможлива.
```bash
for i in 1 2 3; do python -m module_3_workflows.src.workflow_engine event hitl signed --event-id dia-evt-1; done
```
Очікувано: рівно одна виплата, два повтори позначені як дубль.

### 4. Saga: компенсація при скасуванні (друга гілка тієї самої точки e-sign)

`compensation_node` виконує у зворотному порядку: `cancel_contract` (договір `voided`), потім `release_limit` (ліміт `released`) над таблицями ledger; виплата non-compensable і стоїть після підпису.
```bash
python -m module_3_workflows.src.workflow_engine start cancel
python -m module_3_workflows.src.workflow_engine event cancel cancelled --event-id c1
python -m module_3_workflows.src.workflow_engine status cancel
```
Очікувано: `limit: released`, `contract: voided`, `disbursements: 0`.

### 5. Запустіть автоматичні тести

```bash
python -m pytest module_3_workflows/tests/ -v
```
7 тестів: пауза та відновлення в новому «процесі», справжній SIGKILL між двома процесами, дедуплікація вебхука, Saga, відхилення комітетом без побічних ефектів. Без жодних моків чекпоінтера.

### 6. Розширення (за бажанням)

Додайте вузол очікування `docs_requested` (ще один `interrupt()`) або таймаут для комітету (наприклад, статус `escalated` за SLA). Напишіть тест, який вбиває процес на новій паузі.

> **Що стаб:** «ядро банку» — це SQLite-таблиці (`Ledger`), а не справжня АБС; скоринг — таблиця з двох тестових ІПН. Механізми (чекпоінт, interrupt, ключ ідемпотентності, Saga) — справжні.

---

## Критерії прийому (Definition of Done)

- [ ] `workflow_engine demo` виконано: код процесу A `-9`, процес B (інший PID) продовжив з паузи комітету.
- [ ] Пояснено, чому після відновлення не повторюються `intake`/`kyc` (стан у `checkpoints.db`, відновлення за `thread_id`).
- [ ] Три однакові вебхуки дали рівно одну виплату; пояснено два рівні захисту (ключ події та `PRIMARY KEY` виплати).
- [ ] Saga: після скасування договір `voided`, ліміт `released`, виплат немає.
- [ ] У відповіді письмово позначено, які точки паузи (docs, offer, sign), умовні HITL (OCR < 0.85, band C) та evaluator gate є у вашому графі, а які лише описані в deck.
- [ ] Усі тести `module_3_workflows/tests/` проходять (`pytest`).

---

## Якщо застрягли

**Як працює пауза в LangGraph і хто «викликає workflow з місця паузи»?**
Звичайна Python-функція. Вузол викликає `interrupt(payload)`: LangGraph зберігає чекпоінт у SQLite і повертає керування з результатом, де є `__interrupt__`. Процес може завершитись. Пізніше будь-який процес відкриває той самий чекпоінтер і викликає `app.invoke(Command(resume=значення), config={"configurable": {"thread_id": ...}})`. Граф бере останній чекпоінт цього `thread_id`, **повторно виконує лише вузол, що чекав** (тому до `interrupt()` не можна робити побічних дій), і `interrupt()` повертає `значення`. Далі граф іде своїми переходами. Вхід вебхука або адмінки — це просто така функція (див. `deliver_event`).

**Чому `thread_id` важливий?** Це ключ, за яким чекпоінтер знаходить заявку; нова заявка — новий `thread_id`.
