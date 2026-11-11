# Module 1 · Agent topology

> **Webinar 1** · 2 години · 50/50 theory/practice

## Що ви навчитесь

Побудуєте **Loan Copilot** як multi-agent систему: Orchestrator + 7
specialists (Intake, KYC, CreditHistory, Underwriter, Pricing, Contract,
Compliance). У коді W1 реалізовано Orchestrator + перші 4 specialists;
ContractAgent додаєте самостійно в домашній лабі. У кінці W1 у вас
працюватиме наскрізний flow від `intake` до `decision` на моках.

Теорію цього блоку дивіться в деку (слайди «W1 Agenda», «W1 Theory»,
«A2A contract», «Scenario», «Loan journey»); live-демо у CodeMie
відповідає частині практики «Loan journey» (`codemie_demo/`).

## Prerequisites

- Python 3.11+ встановлений
- `.env` з `OPENAI_API_KEY` або `ANTHROPIC_API_KEY`
- Прочитаний корінь [`README.md`](../README.md)

## Steps

Читайте по порядку:

1. [01-why-split.md](./01-why-split.md) · чому не монолітний prompt
2. [02-roles-and-contracts.md](./02-roles-and-contracts.md) · 7 ролей, I/O контракти
3. [03-first-agent.md](./03-first-agent.md) · перший agent (Intake)
4. [04-orchestrator.md](./04-orchestrator.md) · supervisor + routing
5. [05-exercise.md](./05-exercise.md) · ваша лаба

## Виконуваний код

```bash
# з root (loan-copilot/)
python -m module_1_agents.src.intake_agent
python -m module_1_agents.src.orchestrator
```

> **Note.** Python packages не можуть містити дефіси, тому
> директорії модулів названі `module_1_agents` (підкреслення),
> не `module-1-agents`.
