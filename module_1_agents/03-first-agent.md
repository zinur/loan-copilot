# 03 · Перший agent · Intake

## Learning objective

Побудуємо `IntakeAgent` з реальним LLM викликом, system prompt'ом та
структурованим output. Запустимо, побачимо, як веде діалог.

## Prerequisites

- [01-why-split.md](./01-why-split.md), [02-roles-and-contracts.md](./02-roles-and-contracts.md)
- `.env` з ключем до LLM provider'а

## Narrative

Intake agent — найпростіший у топології, але повчальний. Його
відповідальність: зібрати 8 полів заявки через діалог з клієнтом.
Коли всі поля є — handoff на `KYCAgent`.

Що робить його production-ready, а не toy?

1. **Narrow scope.** Не відповідає на питання про ставки, рішення,
   продукти. Відмовляється через refusal pattern.
2. **Structured output.** Повертає `AgentResponse`, не рядок. Будь-який
   downstream consumer знає схему.
3. **State awareness.** Знає, які поля вже зібрані (з
   `Application`). Не питає повторно.
4. **Deterministic validation.** Pydantic перевіряє tax_id, email,
   суму. LLM не вирішує, що «1234567890» — валідний tax_id, це робить
   schema.

**Що НЕ робить Intake:**

- не викликає KYC/bureau/etc tools
- не оцінює ризик
- не форматує контракт
- не прощається з клієнтом («дякую за звернення» — задача Orchestrator)

Коли ваш колега напише PR, що розширює Intake функціональністю «а давай
Intake заодно і KYC виклик зробить, це ж один turn клієнта» — ваша
відповідь: ні. Scope creep у multi-agent системах — початок кінця.

## Code

Повний код у [`src/intake_agent.py`](./src/intake_agent.py). Ключові частини:

```python
INTAKE_SYSTEM = """\
Ти Intake Agent для Loan Copilot. Твоя єдина задача — зібрати заявку.

Поля, які треба зібрати (з валідаціями):
  - full_name: ПІБ клієнта
  - tax_id: 10 цифр
  - phone: з кодом країни
  - email: валідний формат
  - monthly_income: грн/міс, число
  - amount: сума кредиту, грн
  - term_months: від 3 до 84
  - purpose: consumer | auto | refinance

Правила:
  1. Запитуй по 1-2 поля за turn, не всі одразу.
  2. Валідуй: якщо tax_id не 10 цифр — попроси виправити.
  3. Якщо клієнт питає про ставки/рішення/продукти — відповідай:
     "Я збираю заявку. Для відповіді на це запитання потрібно
     завершити intake, передам на відповідного спеціаліста."
  4. Коли всі поля зібрано — поверни JSON (ТІЛЬКИ JSON, без тексту):
     {"status": "complete", "collected": { ...всі поля... }}

Поки збираєш — звичайна відповідь текстом.
"""
```

Логіка виклику:

```python
def intake_turn(user_message: str, state: dict) -> AgentResponse:
    messages = [
        {"role": "system", "content": INTAKE_SYSTEM},
        {"role": "system", "content": f"Current state: {json.dumps(state)}"},
        {"role": "user", "content": user_message},
    ]
    result = chat(messages)

    # Детекція структурованого завершення
    if result.content.strip().startswith("{"):
        try:
            payload = json.loads(result.content)
            if payload.get("status") == "complete":
                return AgentResponse(
                    agent="IntakeAgent",
                    content="Заявка зібрана. Передаю на KYC.",
                    next_action="handoff",
                    handoff_to="KYCAgent",
                    data={"intake": payload["collected"]},
                )
        except json.JSONDecodeError:
            pass

    return AgentResponse(
        agent="IntakeAgent",
        content=result.content,
        next_action="ask_user",
    )
```

Два виходи: `ask_user` (ще потрібні дані) або `handoff` (передача на
KYC). Жодних інших — Intake нічого іншого не вміє.

## Try it

```bash
cd loan-copilot
python -m module_1_agents.src.intake_agent
```

Побачите інтерактивний чат. Спробуйте такі сценарії:

**Happy path:**
```
> Привіт, хочу кредит на 100 тис грн на 24 місяці на ремонт.
Agent: Чудово. Для оформлення заявки потрібні ваші дані...
```

**Scope test:**
```
> Яка у вас ставка?
Agent: Я збираю заявку. Для відповіді на це запитання потрібно...
```

**Validation test:**
```
> ПІБ: Марія Коваленко, ІПН: 123
Agent: ІПН має містити рівно 10 цифр...
```

**Completion:**
Введіть всі 8 полів → побачите handoff відповідь.

## Next

[04-orchestrator.md](./04-orchestrator.md) · додамо Orchestrator, який
маршрутизує між Intake → KYC → Bureau → Underwriter.
