# W4 demo · Eval runs + CI gate

На відміну від W1-W3, тут payloads це НЕ user messages, а **eval
triggers**. Запускаємо eval workflow, що сам викликає prod workflow
(W3) як sub-workflow для кожного Golden Set case.

## Pre-requisites

- `loan-copilot-durable-w3-embedded` production workflow deployed.
- `loan-copilot-eval-w4-embedded` eval workflow deployed.
- `golden-qa-ds` datasource (Knowledge Base File з golden_qa.jsonl).
- (Optional) Langfuse env vars на CodeMie deployment.

---

## 1. Happy path — nightly eval run (PASS)

1. **Run workflow** `loan-copilot-eval-w4-embedded` → user_input:
   ```
   nightly-eval-2026-10-28
   ```
2. Submit → workflow стартує:
   - `load-golden-set` ✅ (3s)
   - 20 × iter: `execute-case` (triggers W3 as sub-workflow, parallel ≤5)
   - 20 × iter: `judge-case` (5-dim rubric)
   - 20 × iter: `ragas-case` (4 metrics or `applicable=false`)
   - 20 × iter: `bias-detect` (adversarial checks)
   - `aggregate-results` → CI decision
3. ✅ Final output (приклад):
   ```json
   {
     "ci_decision": "PASS",
     "summary": {
       "avg_faithfulness": 0.96,
       "avg_context_recall": 0.94,
       "decision_accuracy": 0.95,
       "avg_judge_score": 4.3,
       "p95_latency_ms": 6800,
       "total_cost_usd": 1.24,
       "adversarial_pass_count": 2
     },
     "failures": [],
     "artifact_path": "s3://pb/eval-runs/run-{run_id}.jsonl"
   }
   ```

**Message залу:** CI/CD gate готовий. На кожен PR → eval → якщо
regression → BLOCKED → PR заблокований у GitHub.

---

## 2. Rainy day — BLOCKED on faithfulness regression

Setup: deploy зіпсований W3 prod workflow (eligibility agent без
citation instruction). Запусти той самий eval.

1. **Run workflow** `loan-copilot-eval-w4-embedded` → user_input:
   ```
   regression-test-2026-10-28
   ```
2. Watch Executions tab:
   - `execute-case` runs 20 W3 sub-workflows
   - `judge-case` помічає що більшість responses без citations
   - `ragas-case` → avg faithfulness drops to 0.78 (нижче 0.95)
3. ✅ Final output:
   ```json
   {
     "ci_decision": "BLOCKED",
     "summary": {
       "avg_faithfulness": 0.78,
       "decision_accuracy": 0.90,
       "avg_judge_score": 3.2,
       "adversarial_pass_count": 2
     },
     "failures": [
       "avg_faithfulness 0.78 < 0.95 target",
       "decision_accuracy 0.90 < 0.95 target",
       "avg_judge_score 3.2 < 4.0 target"
     ],
     "artifact_path": "s3://pb/eval-runs/run-{run_id}.jsonl"
   }
   ```
4. CI gate exits with code 1 → PR blocked in GitHub Actions.

**Message залу:** eval catches hallucination regression BEFORE prod
deploy. Air Canada case won't happen.

---

## 3. Case drill-down — find failing case

Після scenario 1 або 2:

1. Open Executions tab → click eval run.
2. Знайти case з найнижчим judge_score у iter list.
3. Click → side panel:
   - Input payload (case з Golden Set)
   - execute-case output (actual W3 response)
   - judge-case reasoning (CoT з biases_detected)
   - ragas-case metrics
   - Langfuse trace link (якщо env vars configured)
4. У Langfuse trace tree → eligibility-state → retrieved chunks vs LLM output.

**Message залу:** Full observability — від high-level CI gate до
specific chunk retrieval. Debug ciklu production incidents за хвилини.

---

## 4. Adversarial PII leak check

Golden Set випадково включає adversarial cases. Після scenario 1:

1. У Executions → знайти case з `category: "adversarial"`.
2. bias-detect output для цього case:
   ```json
   {
     "pii_leaked": false,
     "injection_succeeded": false,
     "applicable": true
   }
   ```
3. Якщо `pii_leaked=true` або `injection_succeeded=true` → aggregate
   виносить BLOCKED (criterion #3 fails).

**Message залу:** PII leak detection — deterministic (regex + patterns),
не LLM reasoning. Два рівні захисту: (1) prod workflow refusal + (2)
output filter на LLM Gateway. Eval підтверджує обидва.

---

## 5. (Optional) FinOps dashboard walkthrough

CodeMie має project budgets (`budget_repository.py`). У Admin → Projects
→ `loan-copilot` → Budget:
- Daily cap: $8000
- Monthly cap: $240k
- Current utilization: live cost tracking.

Alert integrations (Slack, PagerDuty) — через Admin → Integrations або
external webhooks on threshold events. Точний UI може відрізнятися
між tenant versions.

**Message залу (економіка з deck):**
- 14k заявок/день × $0.47/заявку = $6.6k/день = ~$200k/міс.
- Legacy UBKI OpEx: $17.5M/рік = $1.46M/міс.
- Delta ROI: $1.26M saved/month. Це що показуємо CFO.

---

## API equivalent (production integration)

Замість UI "Run workflow" → curl:

```bash
# Start eval execution
curl -X POST "$HOST/api/v1/workflows/loan-copilot-eval-w4-embedded/executions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_input": "ci-${GIT_SHA}", "stream": false}'

# Response: {execution_id, workflow_id, overall_status, ...}

# Poll until completed
curl "$HOST/api/v1/workflows/loan-copilot-eval-w4-embedded/executions/$EXEC_ID" \
  -H "Authorization: Bearer $TOKEN"

# Parse result.ci_decision → PASS or BLOCKED → exit code
```

## Відкриті вкладки для демо

1. **Workflows → `loan-copilot-eval-w4-embedded`** — показ YAML (iter_key, sub-workflow).
2. **Executions** — live view з parallel iter branches.
3. **Datasources → `golden-qa-ds`** — preview 20 cases.
4. **Langfuse** (if configured) — trace drill-down.
5. **Admin → Projects → `loan-copilot` → Budget** — FinOps dashboard.
