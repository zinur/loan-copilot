# 05 · Exercise · ваша W1 **домашня** лаба

> ⏱ **~60–90 хв · самостійно після вебінару** · не на живому занятті
> 🤖 **Vibe-code OK** — Claude Code / Cursor / Windsurf / CodeMie Code — ваш вибір; на вебінарі ми показуємо implementation у CodeMie, вдома ви відтворюєте.
> 🎯 Мета: власноруч пройти шлях від теорії до робочого коду, щоб у понеділок ви могли впевнено захистити рішення перед CTO.

## Learning objective

Самостійно побудувати 5-й agent (**ContractAgent**) та інтегрувати
його у Orchestrator. Перевірити ваше розуміння паттерну.

## Prerequisites

Усі попередні steps W1.

## Завдання

### 1. Додайте `ContractAgent`

Створіть файл `src/contract_agent.py`. Agent має:

- Отримувати `Application` з `decision=="approve"` та `rate_apr`.
- Генерувати текст контракту за template (не викликайте LLM для
  форматування таблиці — це робить Jinja).
- Повертати `AgentResponse` з `next_action="handoff"`,
  `handoff_to="ComplianceAgent"`, `data={"contract_text": "..."}`.

### 2. Розширте `route()` в `orchestrator.py`

Додайте перехід: після `Underwriter`, якщо `decision=="approve"` і
немає `contract_text` у state — передавайте на `ContractAgent`.

### 3. Додайте refusal pattern

У `IntakeAgent` зараз є refusal на питання про ставки. **Додайте
refusal для питання «а чи схвалите мені кредит?»** — Intake не має
відповідати ні «так», ні «ні», лише перенаправити.

### 4. Напишіть 3 unit tests для `route()`

У `tests/test_routing.py`:

- test_empty_state → `IntakeAgent`
- test_kyc_failed → `None` (flow припиняється)
- test_full_approval_path → послідовність до `ContractAgent`

### 5. (Bonus) Логування audit trail

Додайте у Orchestrator decorator, що пише кожен state transition у
файл `audit.jsonl`:

```
{"ts": "2026-...", "from": "IntakeAgent", "to": "KYCAgent", "app_id": "LC-..."}
```

Compliance такий файл любить. Без нього у production ви не пройдете
sign-off.

## Критерії прийому

- [ ] `python -m module_1_agents.src.orchestrator` проходить full flow
      до `ContractAgent`
- [ ] `python -m pytest module_1_agents/tests/` — 3 passing tests
- [ ] `IntakeAgent` відмовляється робити pre-approval
- [ ] (bonus) `audit.jsonl` створюється та заповнюється

## Якщо застрягли

**Застрягли на prompt engineering для ContractAgent?** Не вживайте LLM
для табличного форматування. Використайте Python template:

```python
CONTRACT_TEMPLATE = """\
КРЕДИТНИЙ ДОГОВІР №{app_id}
Позичальник: {full_name}
Сума: {amount:,.2f} грн
Термін: {term_months} міс
Ставка (APR): {rate_apr}%
Дата: {date}
"""
```

LLM — лише для вступних формулювань, якщо потрібні. Деталі — Python.

**Застрягли на routing?** Пам'ятайте: кожен if у `route()` — на
власному рядку. Якщо вам треба `and`/`or` з 3+ умов — щось не так зі
schema. Додайте comp'уте property у `Application`.

## Коли готово

Переходьте до [Module 2 · Grounding](../module-2-grounding/README.md).
Там підключимо реальні tools (credit history, KYC) та LLM Gateway замість
прямих викликів.
