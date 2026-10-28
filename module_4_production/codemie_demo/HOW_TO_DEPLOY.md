# Deployment у CodeMie — W4 (Eval + Observability + FinOps)

> **W4 оцінює production workflow з W3.** Переконайся, що
> `loan-copilot-durable-w3-embedded` deployed та проходить smoke test,
> перед тим як почати W4.

## Що додаємо у W4 vs W3

| Компонент | W3 | W4 |
|---|---|---|
| Golden Set | ❌ | ✅ 20 cases у `golden-qa-ds` datasource |
| LLM-as-Judge | ❌ | ✅ `judge` assistant з 5-dim rubric + debiasing |
| RAGAS metrics | ❌ | ✅ 4 metrics (faithfulness/recall/precision/relevance) |
| Bias detection | ❌ | ✅ PII leak + injection detection для adversarial |
| CI gate | ❌ | ✅ PASS/BLOCKED decision з 7 criteria |
| Sub-workflow invocation | ❌ | ✅ Native `workflow_id` field — тригерить W3 як child |

## Чеклист деплою (~20 хв)

### Крок 1. Створи `golden-qa-ds` datasource (~5 хв)

У CodeMie → **Datasources → + Create Datasource**:

| Поле | Значення |
|------|----------|
| Name | `golden-qa-ds` |
| **Description** | `Golden Set 20 cases для автоматичної оцінки Loan Copilot: 10 Eligibility (POL/SOP/REG refusals), 5 Risk (band A/B/C/D), 3 KYC (sanctions/PEP/docs), 2 Adversarial (prompt injection + PII leak attempt). Структура case: {case_id, category, input_payload, expected_decision, expected_citations}.` |
| **Type** | **`Knowledge Base File`** (internal: `knowledge_base_file`) |
| **Embedding model** | `text-embedding-ada-002` (Ada · 1536-dim) |
| Chunk size | 1024 (один case = один chunk) |

> **Note:** CodeMie не має окремого "JSONL collection" datasource типу
> (verified у `core/constants.py` DatasourceTypes). Використовуємо
> `Knowledge Base File` з `golden_qa.jsonl` як вихідний файл. Для
> structured retrieval потрібен custom tool wrapper (альтернатива — DB-
> backed datasource через MCP server).

**Upload:** `module_4_production/data/golden_qa.jsonl` (20 cases).

> **Datasource versioning:** на момент перевірки (26.10) CodeMie **не
> має feature versioning datasource**. Для baseline comparison — створи
> окремий datasource `golden-qa-ds-v2` при breaking changes, або
> зберігай version markers у metadata polів.

### Крок 2. Створи 5 нових W4 workers (~5 хв)

Якщо використовуєш `eval_workflow.yaml.alt-embedded` (recommended) —
**нічого не створюй**, всі 5 assistants inline у workflow YAML.

Якщо reference-based `eval_workflow.yaml.alt-sequential` — створи:

| Slug | Prompt source | Model | Temp | Attach |
|---|---|---|---|---|
| `eval-loader-agent` | inline (loader section) | fast | 0.0 | Datasource `golden-qa-ds` |
| `llm-judge-agent` | [`ws_judge.prompt.md`](./ws_judge.prompt.md) | **reasoning tier · ІНШИЙ provider** | 0.0 | (none) |
| `ragas-scorer-agent` | inline (ragas section) | fast | 0.0 | Tool `ragas.compute` (optional) |
| `bias-detector-agent` | inline (bias_detector section) | fast | 0.0 | (none, deterministic reasoning) |
| `eval-aggregator-agent` | inline (aggregator section) | fast | 0.0 | (none) |

**КРИТИЧНО для judge:** different LLM family vs agent-under-test.
Якщо prod workflow на Azure OpenAI → judge на Anthropic Claude (via DIAL).
Інакше self-enhancement bias.

### Крок 3. Створи W4 tools (optional) (~5 хв)

У embedded variant кожен assistant робить tool simulation у prompt
(RAGAS metrics, PII detection) — tools не обов'язкові для demo.

Для production:

| Tool | Side-effect | Implementation |
|---|---|---|
| `ragas.compute` | internal | Python wrapper over RAGAS library (MCP server) |
| `detect_pii_leak` | internal | Regex Luhn + patterns + NER classifier (MCP) |
| `detect_injection_success` | internal | Pattern matcher (e.g. "SYSTEM:" + "Override") |

> **НЕ потрібен** `trigger_production_workflow` tool — у embedded YAML
> використовується native `workflow_id` field на execute-case state,
> що викликає W3 workflow як sub-workflow (verified у
> `workflows/nodes/sub_workflow_node.py`).

### Крок 4. Deploy eval workflow YAML (~2 хв)

У **Workflows → + Create Workflow → YAML**:

Copy-paste [`eval_workflow.yaml.alt-embedded`](./eval_workflow.yaml.alt-embedded).
Save → slug = `loan-copilot-eval-w4-embedded`.

**Pre-check:** `loan-copilot-durable-w3-embedded` вже deployed та
accessible (sub-workflow invocation вимагає цього).

### Крок 5. CI/CD integration (~3 хв)

**GitHub Actions / GitLab CI** → додай step:

```yaml
# .github/workflows/eval-gate.yml
name: Loan Copilot Eval Gate
on: [pull_request, workflow_dispatch]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger CodeMie eval workflow
        env:
          CODEMIE_API: ${{ secrets.CODEMIE_API }}
          CODEMIE_TOKEN: ${{ secrets.CODEMIE_TOKEN }}
        run: |
          # POST start execution
          RESPONSE=$(curl -X POST \
            "$CODEMIE_API/api/v1/workflows/loan-copilot-eval-w4-embedded/executions" \
            -H "Authorization: Bearer $CODEMIE_TOKEN" \
            -H 'Content-Type: application/json' \
            -d '{"user_input": "nightly-eval-${{ github.sha }}", "stream": false}')

          EXEC_ID=$(echo $RESPONSE | jq -r .execution_id)

          # Poll for completion
          while true; do
            STATUS=$(curl -s \
              "$CODEMIE_API/api/v1/workflows/loan-copilot-eval-w4-embedded/executions/$EXEC_ID" \
              -H "Authorization: Bearer $CODEMIE_TOKEN" | jq -r .overall_status)
            [ "$STATUS" = "completed" ] && break
            [ "$STATUS" = "failed" ] && exit 1
            sleep 30
          done

          # Fetch result → check ci_decision
          DECISION=$(curl -s \
            "$CODEMIE_API/api/v1/workflows/loan-copilot-eval-w4-embedded/executions/$EXEC_ID" \
            -H "Authorization: Bearer $CODEMIE_TOKEN" | jq -r '.result.ci_decision')

          [ "$DECISION" = "PASS" ] && exit 0 || exit 1
```

### Крок 6. Scheduled nightly run (~2 хв)

CodeMie підтримує cron triggers для workflows (verified у
`triggers/bindings/cron.py` — apscheduler + croniter backend).

У CodeMie UI → **Workflows → `loan-copilot-eval-w4-embedded` → Triggers →
+ Create Trigger**:
- Type: **Cron**
- Cron expression: `0 2 * * *` (2:00 nightly)
- Prompt: `nightly-eval-{date}` (template string)

> Якщо UI не має trigger створення — попроси DevOps створити через
> trigger API (POST /api/v1/triggers).

### Крок 7. Langfuse observability (~1 хв)

**Langfuse у CodeMie — платформа-рівень config, не tenant UI toggle.**
`src/codemie/enterprise/langfuse/dependencies.py:initialize_langfuse_from_config`
читає env vars:
- `OBSERVABILITY_PROVIDER=langfuse`
- `LANGFUSE_HOST=http://langfuse.<pb-internal>:3000`
- `LANGFUSE_PUBLIC_KEY=...`
- `LANGFUSE_SECRET_KEY=...`

Якщо env vars налаштовані на CodeMie deployment — всі workflow traces
автоматично exportуються. У tenant UI нічого робити не треба.

Verify traces: Langfuse → Traces → filter by `project=loan-copilot` або
`workflow_id=loan-copilot-eval-w4-embedded`.

### Крок 8. Budget tracking (~2 хв)

CodeMie має **project budgets** (verified у `repository/budget_repository.py`
+ `project_spend_tracking_repository.py`).

У CodeMie UI → **Admin → Projects → `loan-copilot` → Budget**:
- Daily cap: `$8000` (14k × $0.47 + 20% buffer).
- Monthly cap: `$240k`.
- Alerts: thresholds configurable per deployment.

> Точні UI пути можуть відрізнятися між tenant versions. Alert
> integrations (Slack, PagerDuty) — у Admin → Integrations або через
> external webhook on cost threshold events.

---

## Pre-stage checklist (перед live demo)

- [ ] `golden-qa-ds` datasource (Knowledge Base File) з 20 cases готовий
- [ ] W3 workflow `loan-copilot-durable-w3-embedded` deployed та проходить smoke test
- [ ] W4 eval workflow `loan-copilot-eval-w4-embedded` deployed
- [ ] (Optional) Judge model з different provider ніж agent-under-test
- [ ] CI integration у GitHub Actions / GitLab (test з dummy PR)
- [ ] Langfuse env vars налаштовані на CodeMie deployment (SRE-side)
- [ ] Scheduled nightly run cron trigger створений
- [ ] Backup: VSCode з `python -m module_4_production.src.run_eval` готовий

## Поширені проблеми

**"Sub-workflow invocation fails"** → `workflow_id` на execute-case
state не знаходить child workflow. Перевір, що `loan-copilot-durable-w3-embedded`
точно deployed (slug точно co-pастає).

**"Judge стабільно дає 5 за all cases"** → judge model з того ж family
що agent-under-test. Self-enhancement bias. Swap провайдера у судді.

**"RAGAS metrics завжди null"** → agent returns `applicable=false` для
non-RAG cases. Для RAG cases (eligibility) має бути non-null. Якщо
null — перевір output_schema та prompt.

**"Eval workflow крутиться вічно на `iter_key: cases`"** → sub-workflow
не завершується (HITL pauses у W3). Для eval runs — W3 має завершитися
автономно (без real human). Варіанти:
1. У eval mode W3 має simulated HITL answers (via workflow context flag).
2. Edit output для кожного `webhook_*` state через API callback.
3. Створити spin-off W3-eval варіант з auto-approved HITL stubs.

**"PII leak detector catches amount=1234567890 as fake ІПН"** →
false positive. Додай context до regex: ІПН pattern вимагає "ІПН"
word nearby (3-token window).

## Міграційний шлях eval

**Phase 1:** eval у dev-only як sanity check (ручний trigger).
**Phase 2:** nightly scheduled, повідомлення у Slack, не блокує deploy.
**Phase 3:** CI gate on PR — BLOCKED → merge заблокований.
**Phase 4:** production monitoring — не Golden Set, а sample з live traffic
(1% canary) → scored асинхронно → drift alerts.

Поточний W4 setup = Phase 3. Phase 4 — у стандарті виходу з курсу.
