# CodeMie live demo · W4 — Eval + Observability + FinOps

Готові артефакти для live-демо на W4 вебінарі: автоматизований Golden
Set eval, LLM-as-Judge з debiasing, RAGAS metrics, CI gate decision,
FinOps dashboard.

## Архітектура

W4 workflow **НЕ замінює** prod workflow з W3
(`loan-copilot-durable-w3-embedded`). Воно оцінює prod workflow проти
Golden Set, запускаючи W3 як **native sub-workflow** через `workflow_id`
field:

```
┌─────────────────────────────────────────┐
│ loan-copilot-eval-w4-embedded (W4)      │
│ · load-golden-set (iter_key: cases)     │
│ · for each case:                        │
│   └─ execute-case                       │ ◀── workflow_id triggers W3
│      (workflow_id: w3-embedded)         │     as sub-workflow,
│   └─ judge-case                         │     awaits completion
│   └─ ragas-case                         │
│   └─ bias-detect                        │
│ · aggregate-results                     │
│   → CI gate decision (PASS/BLOCKED)     │
└─────────────────────────────────────────┘
```

## Файли

| Файл | Що це |
|------|-------|
| **[`HOW_TO_DEPLOY.md`](./HOW_TO_DEPLOY.md)** | Основна інструкція — golden-qa-ds datasource + embedded workers + CI integration + Langfuse config + budget tracking |
| **[`eval_workflow.yaml.alt-embedded`](./eval_workflow.yaml.alt-embedded)** | **Основний артефакт** (recommended) — all 5 assistants inline, native sub-workflow via `workflow_id`, English JSON internals |
| [`eval_workflow.yaml.alt-sequential`](./eval_workflow.yaml.alt-sequential) | Legacy reference-based variant (external assistant_ids, custom tool for sub-workflow) |
| [`ws_judge.prompt.md`](./ws_judge.prompt.md) | System prompt для LLM-as-Judge (5-dim rubric + debiasing techniques) |
| [`demo_payloads.md`](./demo_payloads.md) | 4 scenarios: nightly PASS run, BLOCKED regression, case drill-down, adversarial check |
| [`demo_script.md`](./demo_script.md) | 15-хв сценарій живого показу |

## 6-state eval workflow map

```
1. load-golden-set
   ↓ (iter_key: cases → fan-out, max_concurrency=5)

[For each case — паралельно]
2. execute-case (workflow_id: w3 → sub-workflow)
3. judge-case (5-dim rubric, debiased)
4. ragas-case (4 math metrics, if RAG-based)
5. bias-detect (PII leak + prompt injection для adversarial)
   ↓

6. aggregate-results → CI PASS/BLOCKED decision + artifact
```

## 7 CI gate criteria (ВСІ має бути true для PASS)

| # | Criterion | Target |
|---|---|---|
| 1 | Avg faithfulness (RAG cases) | ≥ 0.95 |
| 2 | Avg context_recall (RAG cases) | ≥ 0.92 |
| 3 | Adversarial pass rate | 100% (2/2) |
| 4 | Decision accuracy | ≥ 95% (19/20) |
| 5 | Avg LLM-judge overall score | ≥ 4.0 (з 5) |
| 6 | P95 latency | < 8s |
| 7 | Total run cost | < $5 |

## Pre-stage checklist

- [ ] W3 workflow `loan-copilot-durable-w3-embedded` deployed та проходить smoke test
- [ ] Datasource `golden-qa-ds` (Knowledge Base File) з 20 cases
- [ ] W4 workflow `loan-copilot-eval-w4-embedded` deployed (all 5 assistants inline)
- [ ] (Optional) Judge model з different provider ніж agent-under-test
- [ ] CI integration у GitHub Actions / GitLab (test з dummy PR)
- [ ] Langfuse env vars налаштовані на CodeMie deployment (SRE-side)
- [ ] Scheduled nightly run cron trigger створений
- [ ] Backup: VSCode з `python -m module_4_production.src.run_eval`

## 4 pattern'и, які зал має запам'ятати

1. **Eval як окремий workflow, не side-effect prod**. Prod має свій SLO
   (latency < 8s). Eval має свій (cost < $5/run). Different owners.

2. **LLM-as-Judge з debiasing rubric**. Position bias (A/B swap),
   Verbosity bias (-1 clarity for > 500 words), Self-enhancement
   (different provider).

3. **RAGAS != LLM reasoning**. Faithfulness — математична формула
   |verified| / |total|. Deterministic. Calibrated proti human-rated.

4. **CI gate = binary decision**. PASS або BLOCKED. 7 criteria все
   true. Нема "approve with warning" — merge блокується якщо хоч одна
   fails.

## CodeMie fact-check (verified from source)

| Feature | Status |
|---|---|
| `iter_key: cases` fan-out syntax | ✅ Real (`workflow_generator/schemas.py`) |
| `workflow_id` state → sub-workflow invocation | ✅ Real (`SubWorkflowNode`) |
| Workflow cron scheduling | ✅ Real (`triggers/bindings/cron.py`) |
| Project budgets | ✅ Real (`budget_repository.py`) |
| Datasource type `Knowledge Base File` | ✅ Real (`FullDatasourceTypes.FILE`) |
| **Langfuse integration** | ⚠️ **Env var config, НЕ tenant UI** |
| **Datasource versioning** | ❌ **Не існує** як feature |
| **"JSONL collection" datasource type** | ❌ **Не існує** — використовувати Knowledge Base File |

## Пов'язано

- Домашнє завдання: [`../02-exercise.md`](../02-exercise.md) — Python
  еквівалент eval runner + Langfuse docker setup.
- CI/CD Eval slide у deck: slide #41.
- W3 CodeMie demo (prod workflow що оцінюємо): [`../../module_3_workflows/codemie_demo/`](../../module_3_workflows/codemie_demo/).
- Golden Set JSONL: `../data/golden_qa.jsonl`.
