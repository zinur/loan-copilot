# CodeMie live demo · W2 — Grounded Loan Copilot (RAG + Tools + Gateway)

Готові артефакти для live-демо на W2 вебінарі: додаємо grounding (RAG на
банківських регламентах), 3 real tools з Consent Gate, та LLM Gateway
налаштування понад Loan Copilot з W1.

## Файли

| Файл | Що це |
|------|-------|
| **[`HOW_TO_DEPLOY.md`](./HOW_TO_DEPLOY.md)** | **Основна інструкція** — datasource + 2 нових workers + 3 tools + gateway + guardrails |
| [`ws_eligibility.prompt.md`](./ws_eligibility.prompt.md) | System prompt для Eligibility Agent (RAG + citation discipline) |
| [`ws_risk.prompt.md`](./ws_risk.prompt.md) | System prompt для Risk Agent (3 tools + Consent Gate) |
| [`demo_payloads.md`](./demo_payloads.md) | 4 сценарії для демо (happy / FOP refusal / consent block / sanctions block) + bonus prompt-injection |
| [`demo_script.md`](./demo_script.md) | 10-хв сценарій живого показу |
| [`tool_contracts.yaml.alt`](./tool_contracts.yaml.alt) | **Alternative:** OpenAI function schemas для 3 tools + mock responses (production: swap на MCP server/UBKI client) |
| [`grounded_copilot_workflow.yaml.alt-sequential`](./grounded_copilot_workflow.yaml.alt-sequential) | **Alternative:** deterministic Sequential workflow з `interrupt_before` на Consent Gate (для NBU-audited production) |
| [`gateway_config.yaml.alt`](./gateway_config.yaml.alt) | **Reference:** LLM Gateway config (DIAL on-prem / Azure OpenAI EU, PII masking, budgets, Langfuse) |

## Що додаємо у W2 vs W1

| Шар | W1 | W2 |
|---|---|---|
| **Grounding** | ❌ | ✅ Hybrid RAG на 3 документах (POL-LOAN-01, SOP-CREDIT-02, REG-NBU-351) з citation discipline |
| **Real tools** | ❌ (lookup tables у prompt) | ✅ 3 tools: `kyc.lookup`, `credit_history.pull` (з consent), `risk.score` |
| **Consent Gate** | ❌ | ✅ `interrupt_before: true` + tool-level enforcement |
| **LLM Gateway** | ⚠️ default (public) | ✅ DIAL on-prem / Azure EU, PII masking, budgets |
| **Guardrails** | ❌ | ✅ Input (injection), DLP, Output (toxicity, price commitment) |

## Pre-stage checklist (Option B — sub-assistants)

- [ ] Datasource `loan-policies-ds` залитий, test query повертає POL-LOAN-01 §4.2.1 на Top-1
- [ ] 2 нових workers створені: `eligibility-agent` (attached DS), `risk-agent` (attached tools)
- [ ] 3 tools у CodeMie Tools registry (або inline mock у system prompt для швидкого demo)
- [ ] LLM Gateway вказує на DIAL on-prem або Azure OpenAI EU private endpoint
- [ ] Project Guardrails: DLP + Input filter + Output filter — усі ON
- [ ] Budget caps: 100 UAH/user/day, $500 project cap, 75% alert
- [ ] Parent-assistant (`loan-copilot-orchestrator` з W1) розширений: +eligibility, +risk у sub-assistants
- [ ] 3 вкладки відкриті: parent chat, Datasource preview, Langfuse trace
- [ ] Backup: VSCode з `python -m module_2_grounding.src.orchestrator_v2` готовий до запуску

## Pattern: Option B (sub-assistants) vs Option A (Sequential workflow)

| Критерій | Option B · `assistant_ids` | Option A · Sequential workflow |
|---|---|---|
| Routing | LLM parent (handoff tools auto-generated) | `next.condition.expression` deterministic |
| HITL | Клієнт пише у chat | `interrupt_before: true` → UI pause |
| Deployment | 6 assistants у UI, 0 YAML | 6 assistants + 1 workflow YAML |
| Audit | LLM reasoning + handoff logs | Node-by-node execution log + state transitions |
| Коли брати | Live demo, exploratory flows | NBU-audited production з durable waits (W3) |

**Для цього вебінару:** Option B — простіше, швидше, continuation з W1.
Option A залишаємо у [`.yaml.alt-sequential`](./grounded_copilot_workflow.yaml.alt-sequential)
як міст у W3, де durable waits стають обов'язковими.

## Пов'язано

- Домашнє завдання: [`../03-exercise.md`](../03-exercise.md) — Python
  реалізація Consent Gate + Citation discipline + 4 scenario end-to-end.
- RAG tuning (опційно): [`../02-rag-tuning-lab.md`](../02-rag-tuning-lab.md).
- W1 CodeMie demo (базовий layer): [`../../module_1_agents/codemie_demo/`](../../module_1_agents/codemie_demo/).
