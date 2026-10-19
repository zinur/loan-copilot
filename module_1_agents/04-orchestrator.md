# 04 · Orchestrator · supervisor pattern

## Learning objective

Побудуємо Orchestrator, що маршрутизує між 4 agent'ами (Intake, KYC,
CreditHistory, Underwriter). Переконаєтеся, що routing — deterministic
(state machine), не LLM.

## Prerequisites

[03-first-agent.md](./03-first-agent.md)

## Narrative

Orchestrator — найбільша пастка у multi-agent системах. Два анти-патерни:

**Анти-патерн 1: «LLM вирішує, кому передати».** Виглядає гарно у демо.
У production — непередбачувана. На 10 000 заявці ви не знаєте, чи
попадете у KYC → CreditHistory чи CreditHistory → KYC. Audit log нечитабельний.
Compliance відмовляє у sign-off.

**Анти-патерн 2: «Моноліт-switch з LLM-wrappers».** Звичайний
`if-elif` без розуміння стану. Легко зламається на non-happy path
(клієнт передумав, прийшов повтор, 3 заявки одночасно).

**Правильно: state machine з deterministic переходами.** Поточний стан
визначається `Application` fields:

```
intake_complete → KYC
KYC passed → CreditHistory
Credit score known → Underwriter
Decision done → (Contract / Compliance / END)
```

LLM використовується **всередині agent'ів** (для розмови з клієнтом,
витягування сенсу з тексту). Routing між agent'ами — чистий Python.

Це важливо з двох причин:
1. **Testability.** Routing покривається 10 unit tests, кожен — 1 рядок.
2. **Observability.** Audit log — чиста послідовність state transitions,
   compliance читає його за хвилину.

### Supervisor vs delegate pattern

У нашому випадку — **supervisor**. Orchestrator володіє `Application`,
викликає agent'ів почергово, застосовує їх `data` зміни. Agent'и
stateless між викликами.

Альтернатива — **delegate pattern:** Orchestrator передає control
агенту повністю, той сам вирішує, коли повернутися. Складніше, але
краще для long-running tasks (W3 — durable workflows).

## Code

Повний код у [`src/orchestrator.py`](./src/orchestrator.py). Серце:

```python
def route(state: Application) -> str | None:
    """Deterministic routing · повертає ім'я наступного agent'а, або None якщо все."""
    if not _intake_complete(state):
        return "IntakeAgent"
    if state.kyc_passed is None:
        return "KYCAgent"
    if not state.kyc_passed:
        return None  # відмова — KYC fail
    if state.credit_score is None:
        return "CreditHistoryAgent"
    if state.decision is None:
        return "UnderwriterAgent"
    return None  # final


def run_once(state: Application, user_msg: str) -> tuple[Application, AgentResponse]:
    next_agent = route(state)
    if next_agent is None:
        return state, AgentResponse(
            agent="Orchestrator", content="Flow завершено.", next_action="done"
        )

    handler = AGENT_REGISTRY[next_agent]
    response = handler(state=state, user_msg=user_msg)

    # Застосовуємо зміни з response.data до state (owner — Orchestrator)
    new_state = _apply_changes(state, response.data)
    return new_state, response
```

Три важливих спостереження:

1. **`route()` — чистий Python.** Нема LLM, нема prompt'ів. Тестується
   як звичайна функція.
2. **`_apply_changes` — єдине місце mutations.** Agent'и повертають
   патчі в `response.data`, Orchestrator вирішує, застосувати чи ні.
3. **`AGENT_REGISTRY`** — dict з іменами → handlers. Додавання нового
   agent'а = 1 запис, нічого не ламається.

## Try it

```bash
cd loan-copilot
python -m module_1_agents.src.orchestrator
```

Побачите demo flow з моками. Виведе щось типу:

```
[Orchestrator] → IntakeAgent
IntakeAgent → "Збираю заявку..." (handoff after full data)
[Orchestrator] applying: intake fields
[Orchestrator] → KYCAgent
KYCAgent → "KYC passed" (synthetic — викликає MOCK_KYC_RESULTS)
[Orchestrator] → CreditHistoryAgent
CreditHistoryAgent → "Score: 720"
[Orchestrator] → UnderwriterAgent
UnderwriterAgent → "Decision: approve, APR 18.5%"
[Orchestrator] done.
```

**Вправи:**

1. Передайте tax_id `9876543210` як вхідний. Побачите, що flow дійде до
   Underwriter, але decision буде `decline` (score 580).
2. Передайте `5555555555` — KYC fail (sanctions), flow припиниться
   після KYCAgent.
3. Додайте новий agent `PricingAgent` між Underwriter та кінцем. Треба:
   - додати handler у `AGENT_REGISTRY`
   - додати перехід у `route()`
   - розширити `Application` полем `rate_tier`

## Next

[05-exercise.md](./05-exercise.md) · ваша фінальна лаба для W1.
