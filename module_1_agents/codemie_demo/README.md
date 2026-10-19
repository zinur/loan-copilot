# CodeMie live demo · Supervisor pattern

Готові артефакти для live-демо під час воркшопу: 1 supervisor-agent + 4
specialist-agent'и, що відтворюють `src/orchestrator.py` у CodeMie UI.

## Файли

| Файл | Що це |
|------|-------|
| `ws_orchestrator.prompt.md` | System prompt для supervisor |
| `ws_intake.prompt.md` | System prompt для IntakeAgent |
| `ws_kyc.prompt.md` | System prompt для KYCAgent |
| `ws_credit_history.prompt.md` | System prompt для CreditHistoryAgent |
| `ws_underwriter.prompt.md` | System prompt для UnderwriterAgent |
| `mock_tools.yaml` | JSON schema для 2 mock tools |
| `demo_payloads.md` | 3 payloads для демо (happy / decline / sanctions) |
| `demo_script.md` | 8-хвилинний сценарій живого показу |
| **`loan_copilot_workflow.yaml`** | **CodeMie Workflow YAML — готовий до Save** |
| **`HOW_TO_DEPLOY.md`** | **Покрокова інструкція для CodeMie 2.59.0 з troubleshooting** |

## Pre-stage checklist

- [ ] Створити 5 assistant'ів у CodeMie з префіксом `ws` (щоб легко видалити)
- [ ] Залити system prompts з `.prompt.md` файлів
- [ ] Створити 2 mock tools за `mock_tools.yaml`, прикріпити до відповідних agent'ів
- [ ] Прив'язати 4 specialist'и як handoff-targets до orchestrator'а
  (або оформити як 4 tools, якщо first-class handoff не підтримується)
- [ ] Відкрити 3 вкладки browser: orchestrator chat, kyc assistant edit view,
  credit_history assistant edit view (для «клік-турне» на хв 1–2)
- [ ] Payloads з `demo_payloads.md` → у буфер / sticky notes
- [ ] Backup: VSCode з `python src/orchestrator.py` готовий до запуску

## Як влаштований supervisor в CodeMie

CodeMie має **native workflow engine** (Visual Editor + YAML), який ідеально
лягає на supervisor pattern. **Orchestrator — це не LLM-assistant**, а
сам workflow engine + Conditional nodes (детерміновані if/else).

Playbook — `HOW_TO_DEPLOY.md`. YAML — `loan_copilot_workflow.yaml`.

Якщо Workflow engine недоступний у вашому тенанті — фоллбеком
використовуй **sub-assistants** (orchestrator-ассистент з LLM routing).
Схема в `demo_script.md`, секція «Backup variant B».
