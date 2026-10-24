# Deployment у CodeMie — W4 (Eval + Observability + FinOps)

> **W4 оцінює production workflow з W3.** Переконайся, що W3 workflow
> (`loan-copilot-durable-w3`) deployed та проходить smoke test, перед
> тим як почати W4.

## Що додаємо у W4 vs W3

| Компонент | W3 | W4 |
|---|---|---|
| Golden Set | ❌ | ✅ 20 cases у `golden-qa-ds` datasource |
| LLM-as-Judge | ❌ | ✅ `llm-judge-agent` з 5-dim rubric + debiasing |
| RAGAS metrics | ❌ | ✅ 4 metrics (faithfulness/recall/precision/relevance) |
| Bias detection | ❌ | ✅ position/verbosity/self-enhancement + PII leak |
| CI gate | ❌ | ✅ Pass/Blocked decision + artifact export |
| FinOps dashboard | ⚠️ (gateway config) | ✅ Live cost tracking + budget alerts |

## Чеклист деплою (~25 хв)

### Крок 1. Створи `golden-qa-ds` datasource (~5 хв)

У CodeMie → **Datasources → + Create Datasource**:

| Поле | Значення |
|------|----------|
| Name | `golden-qa-ds` |
| Type | JSONL collection |
| Versioned | ✅ ON (для baseline comparison) |

**Upload:** `module_4_production/data/golden_qa.jsonl` (20 cases).

Структура кожного case:
```json
{
  "case_id": "QA-001",
  "category": "eligibility",
  "input_payload": {"message": "ФОП 4 міс, consumer, 50k UAH"},
  "expected_decision": "decline",
  "expected_citations": ["POL-LOAN-01 §4.2.1"],
  "expected_metrics_target": {"faithfulness": 0.95, "context_recall": 0.92}
}
```

### Крок 2. Створи 6 нових W4 workers (~8 хв)

| Slug | Prompt | Model | Temp | Attach |
|---|---|---|---|---|
| `eval-loader-agent` | — datasource reader | fast | 0.0 | Datasource `golden-qa-ds` |
| `eval-executor-agent` | — тонкий proxy до prod workflow | fast | 0.0 | Tool `trigger_production_workflow` |
| `llm-judge-agent` | [`ws_judge.prompt.md`](./ws_judge.prompt.md) | **reasoning tier · ІНШИЙ provider** | 0.0 | (none) |
| `ragas-scorer-agent` | — RAGAS Python wrapper | fast | 0.0 | Tool `ragas.compute` |
| `bias-detector-agent` | — adversarial check | fast | 0.0 | Tools `detect_pii_leak`, `detect_injection_success` |
| `eval-aggregator-agent` | — CI gate decision | fast | 0.0 | (none) |

**КРИТИЧНО для judge:** different LLM family vs agent-under-test.
Якщо prod workflow на Azure OpenAI → judge на Anthropic Claude (via DIAL).
Інакше self-enhancement bias.

### Крок 3. Створи 4 нових W4 tools (~5 хв)

| Tool | Side-effect | Implementation |
|---|---|---|
| `trigger_production_workflow` | internal (triggers CodeMie workflow) | HTTP call to CodeMie Workflows API |
| `ragas.compute` | internal | Python wrapper over RAGAS library (MCP server) |
| `detect_pii_leak` | internal (text analysis) | Regex (Luhn + patterns) + NER classifier |
| `detect_injection_success` | internal | Pattern matcher (е.g. "overridden policy") |

### Крок 4. Deploy eval workflow YAML (~3 хв)

У **Workflows → + Create Workflow → YAML**:

Copy-paste [`eval_workflow.yaml.alt-sequential`](./eval_workflow.yaml.alt-sequential).
Save → slug = `loan-copilot-eval-w4`.

### Крок 5. Налаштуй CI/CD integration (~5 хв)

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
        run: |
          RESPONSE=$(curl -X POST \
            $CODEMIE_API/api/v1/workflows/loan-copilot-eval-w4/executions \
            -H "Authorization: Bearer $CODEMIE_TOKEN" \
            -H 'Content-Type: application/json' \
            -d '{"golden_set_version": "v1", "triggered_by": "github-${{ github.sha }}"}')

          EXECUTION_ID=$(echo $RESPONSE | jq -r .execution_id)

          # Poll for completion
          while true; do
            STATUS=$(curl -s $CODEMIE_API/api/v1/workflows/.../executions/$EXECUTION_ID | jq -r .status)
            [ "$STATUS" = "completed" ] && break
            [ "$STATUS" = "failed" ] && exit 1
            sleep 10
          done

          # Check CI decision
          DECISION=$(curl -s .../$EXECUTION_ID/result | jq -r .ci_decision)
          [ "$DECISION" = "PASS" ] && exit 0 || exit 1
```

### Крок 6. Налаштуй Langfuse export (~2 хв)

У **Admin → Observability → Langfuse** → перевір:
- Endpoint: `http://langfuse.<pb-internal>:3000`
- Sample rate: `1.0` для eval runs (100% capture).
- Project: `loan-copilot-eval`.

Eval workflow traces писатимуться окремо від prod traces.

### Крок 7. Налаштуй budget alerts для FinOps dashboard (~2 хв)

У **Admin → Billing → Project `loan-copilot`**:

- Daily budget cap: `$8000` (14k × $0.47 + 20% buffer).
- Alert thresholds:
  - 75% utilization → Slack `#ai-finops`.
  - 95% utilization → PagerDuty.
  - 100% → hard stop + alert CTO.

### Крок 8. Scheduled nightly run (~1 хв)

У **Workflows → `loan-copilot-eval-w4` → Schedule**:
- Cron: `0 2 * * *` (2:00 UA time nightly).
- Golden Set version: `v1` (lock during cycle).
- Report: Email + Slack `#ai-releases` з summary.

---

## Pre-stage checklist (перед live demo)

- [ ] `golden-qa-ds` datasource з 20 cases готовий
- [ ] 6 нових W4 workers створені
- [ ] 4 нових W4 tools у registry
- [ ] Judge model з different provider ніж agent-under-test
- [ ] Eval workflow YAML deployed, slug `loan-copilot-eval-w4`
- [ ] CI integration у GitHub Actions / GitLab (test з dummy PR)
- [ ] Langfuse eval project separate
- [ ] FinOps alerts налаштовані
- [ ] Scheduled nightly run enabled
- [ ] Backup: VSCode з `python -m module_4_production.src.run_eval` готовий

## Поширені проблеми

**"Judge стабільно дає 5 за all cases"** → judge model з того ж family
що agent-under-test. Self-enhancement bias. Swap провайдера.

**"RAGAS compute timeouts"** → bge-m3 embedding model slow. Fall back
to text-embedding-ada-002 for RAGAS (не для production RAG).

**"PII leak detector catches amount=1234567890 as fake ІПН"** →
false positive. Додай context до regex: ІПН pattern вимагає "ІПН"
word nearby (3-token window).

**"Eval workflow крутиться вічно на `iter_key: cases`"** → prod workflow
timeout. Set `max_wait_duration: 60` на execute-case state. Якщо
prod workflow не завершується за 60s → mark case як failed, continue.

**"Cost per eval run > $5"** → judge model дорогий, cases довгі.
Mitigation: використовуй fast-tier для simple cases (eligibility),
reasoning-tier тільки для adversarial. Router у executor-agent.

## Міграційний шлях eval

**Phase 1:** eval у dev-only як sanity check (ручний trigger).
**Phase 2:** nightly scheduled, повідомлення у Slack, не блокує deploy.
**Phase 3:** CI gate on PR — BLOCKED → merge заблокований.
**Phase 4:** production monitoring — не Golden Set, а sample з live traffic
(1% canary) → scored асинхронно → drift alerts.

Поточний W4 setup = Phase 3. Phase 4 — у стандарті виходу з курсу.
