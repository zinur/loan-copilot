"""IntakeAgent · перший agent системи.

Відповідальність: зібрати 8 полів заявки через розмовний інтерфейс.
Нічого іншого — коли все зібрано, handoff на KYCAgent.

Запуск:
    python -m module_1_agents.src.intake_agent
"""

from __future__ import annotations

import json
import sys

from common.llm_gateway import chat
from common.schemas import AgentResponse

INTAKE_SYSTEM = """\
Ти Intake Agent для Loan Copilot. Твоя єдина задача — зібрати заявку.

Поля, які треба зібрати (з валідаціями):
  - full_name: ПІБ клієнта
  - tax_id: 10 цифр
  - phone: з кодом країни, напр. +380501234567
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
  4. Коли ВСІ 8 полів зібрано — поверни ТІЛЬКИ JSON (без тексту):
     {"status": "complete", "collected": {<всі поля>}}

Поки збираєш — звичайна відповідь текстом.
"""


def intake_turn(user_message: str, state: dict) -> AgentResponse:
    """Один turn Intake agent'а."""
    messages = [
        {"role": "system", "content": INTAKE_SYSTEM},
        {
            "role": "system",
            "content": f"Current collected state: {json.dumps(state, ensure_ascii=False)}",
        },
        {"role": "user", "content": user_message},
    ]
    result = chat(messages)

    text = result.content.strip()

    # Детекція структурованого завершення
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            if payload.get("status") == "complete":
                return AgentResponse(
                    agent="IntakeAgent",
                    content="Заявка зібрана. Передаю на KYC.",
                    next_action="handoff",
                    handoff_to="KYCAgent",
                    data={"intake": payload["collected"]},
                )
        except json.JSONDecodeError:
            pass  # LLM повернув текст з "{" — трактуємо як звичайний

    return AgentResponse(
        agent="IntakeAgent",
        content=text,
        next_action="ask_user",
    )


def _repl() -> None:
    """Інтерактивний чат з Intake agent'ом."""
    print("IntakeAgent REPL. Напишіть 'exit' для виходу.\n")
    state: dict = {}
    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user.lower() in {"exit", "quit", ":q"}:
            break
        if not user:
            continue
        response = intake_turn(user, state)
        print(f"\nAgent: {response.content}\n")
        if response.next_action == "handoff":
            print(f"[handoff → {response.handoff_to}]")
            print(f"[collected data] {json.dumps(response.data, ensure_ascii=False, indent=2)}")
            break
        state.update(response.data)


if __name__ == "__main__":
    sys.exit(_repl())
