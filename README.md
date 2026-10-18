# Loan Copilot · Workshop code

Repository для 4-тижневого воркшопу **AI-архітектор у фінансах**. Супроводжує deck
`CodeMie_Loan_Copilot_Workshop_UA.pptx`.

## Що ви побудуєте

Chat-first copilot для consumer loan: `intake → KYC → bureau → decision →
contract → e-sign`. Має пройти аудит НБУ та EU AI Act, вижити нічний
трафік, не галюцинувати ставки.

## Модулі

| Модуль | Тема | Коли | Статус |
|--------|------|------|--------|
| [module_1_agents](./module_1_agents/README.md) | Agent topology · orchestrator + 7 specialists | Webinar 1 | ✓ |
| [module_2_grounding](./module_2_grounding/README.md) | RAG · tools · LLM gateway | Webinar 2 | stub |
| [module_3_workflows](./module_3_workflows/README.md) | Durable workflows · HITL · pause/resume | Webinar 3 | stub |
| [module_4_production](./module_4_production/README.md) | Eval · observability · cost · security | Webinar 4 | stub |

Кожен модуль — набір `.md` файлів з наративом + runnable Python snippets.
Читайте по порядку: `01-*.md`, `02-*.md`, ...

## Як запустити

### 1. Environment

Потрібен **Python 3.11+**.

```bash
cd loan-copilot
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Secrets

```bash
cp .env.example .env
# Відредагуйте .env — додайте один з:
#   OPENAI_API_KEY=sk-...
#   ANTHROPIC_API_KEY=sk-ant-...
```

Для демо-коду достатньо однієї моделі. LLM Gateway (common/llm_gateway.py)
автоматично підбере те, що знайде.

### 3. Запуск прикладів

```bash
python -m module-1-agents.src.intake_agent          # пряме виконання
python -m module-1-agents.src.orchestrator           # full flow
```

Усі приклади **самодостатні** — не потребують бази даних чи
додаткового інфра. Моковані дані у `common/scenario_data/`.

## Структура

```
loan-copilot/
├── README.md                       <- ви тут
├── requirements.txt
├── .env.example
├── pyproject.toml
├── common/                         <- shared code між модулями
│   ├── schemas.py                  <- Pydantic моделі домену
│   ├── llm_gateway.py              <- тонкий wrapper
│   └── scenario_data/              <- mock klient, bureau, SOP
├── module_1_agents/
│   ├── README.md
│   ├── 01-why-split.md
│   ├── 02-roles-and-contracts.md
│   ├── 03-first-agent.md
│   ├── 04-orchestrator.md
│   ├── 05-exercise.md
│   └── src/
├── module_2_grounding/README.md    <- stub
├── module_3_workflows/README.md    <- stub
└── module_4_production/README.md   <- stub
```

## Філософія коду

- **Короткі snippets.** Жоден приклад > 60 рядків. Довгий код → погане
  навчання.
- **Pydantic скрізь.** I/O контракти — закон. Без них multi-agent систему
  неможливо тестувати.
- **Моковані tools.** Не чіпаємо справжні bureau, НБУ, e-sign. Усі
  зовнішні виклики — у `common/scenario_data/`.
- **One LLM per module.** Не вибираємо між 10 моделями на кожному step.
  LLM Gateway задає абстракцію — конкретні моделі деталі.

## Ліцензія

Приватний learning repository. Код можна копіювати у власні проекти.
