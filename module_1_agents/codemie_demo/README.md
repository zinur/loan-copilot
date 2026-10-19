# CodeMie live demo · Supervisor pattern (Option B — sub-assistants)

Готові артефакти для live-демо під час воркшопу: 1 parent orchestrator-agent
+ 4 worker-agents, зібрані через **`assistant_ids`** (CodeMie-native
LangGraph handoff supervisor).

## Файли

| Файл | Що це |
|------|-------|
| **`HOW_TO_DEPLOY.md`** | **Основна інструкція для деплою через sub-assistants** |
| `ws_orchestrator.prompt.md` | System prompt для parent orchestrator |
| `ws_intake.prompt.md` | System prompt для IntakeAgent |
| `ws_kyc.prompt.md` | System prompt для KYCAgent (з mock lookup-table) |
| `ws_credit_history.prompt.md` | System prompt для CreditHistoryAgent (з mock lookup-table) |
| `ws_underwriter.prompt.md` | System prompt для UnderwriterAgent |
| `demo_payloads.md` | 4 payloads для демо (approve / decline-score / sanctions / injection) |
| `demo_script.md` | 8-хвилинний сценарій живого показу |
| `mock_tools.yaml.alt-with-tools` | **Alternative:** OpenAI function schemas + mock responses, якщо захочеться свопнути inline lookup на реальні tools (підготовка до М2 з real bureau API) |
| `loan_copilot_workflow.yaml.alt-sequential` | **Alternative:** deterministic Sequential workflow (для compliance-first use cases) |

## Pre-stage checklist (Option B)

- [ ] Створити 4 worker assistants у CodeMie: `intake-agent`, `kycagent`,
      `credithistoryagent`, `underwriter` (slugs точно такі)
- [ ] Залити system prompts з `.prompt.md` файлів
- [ ] Кожному worker-у — **Description** (видно у handoff-tool schema)
- [ ] Temperature: 0.1 для intake, **0.0** для kyc/credit, 0.0 для underwriter
- [ ] Shared with Project = ON для всіх
- [ ] Створити parent `loan-copilot-orchestrator` з `ws_orchestrator.prompt.md`
- [ ] У parent → **Sub-assistants** multi-select → додати всі 4 workers
- [ ] Відкрити 3 вкладки browser: parent chat, worker `kycagent` edit view,
      parent edit view (для «клік-турне» на хв 1–2)
- [ ] Payloads з `demo_payloads.md` → у буфер / sticky notes
- [ ] Backup: VSCode з `python -m module_1_agents.src.orchestrator` готовий до запуску

## Чому `assistant_ids`, а не Sequential workflow

Коротко — CodeMie-native modern supervisor pattern. Детально:
[`HOW_TO_DEPLOY.md`](./HOW_TO_DEPLOY.md) розділ "Чому саме `assistant_ids`".

Якщо для вашого production-кейсу (NBU audit, compliance-first) потрібне
**deterministic routing** — беріть
[`loan_copilot_workflow.yaml.alt-sequential`](./loan_copilot_workflow.yaml.alt-sequential)
як стартову точку.
