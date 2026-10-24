# CodeMie live demo · W4 — Eval + Observability + FinOps

Готові артефакти для live-демо на W4 вебінарі: автоматизований Golden
Set eval, LLM-as-Judge з debiasing, RAGAS metrics, CI gate decision,
FinOps dashboard.

## У W4 вводимо окремий `eval_workflow` паралельно до prod

W4 workflow **НЕ замінює** prod workflow з W3 (`loan-copilot-durable-w3`).
Воно оцінює prod workflow проти Golden Set:

```
┌─────────────────────────────────┐          ┌──────────────────────────┐
│ loan-copilot-eval-w4 (W4)       │ ─────▶   │ loan-copilot-durable-w3   │
│ · load Golden Set (20 cases)    │ triggers │ (prod workflow)           │
│ · for each → trigger prod       │          │                           │
│ · LLM-as-judge scoring          │ ◀─────   │ returns actual_output     │
│ · RAGAS metrics                 │  output  │                           │
│ · bias detection                │          │                           │
│ · CI gate decision              │          └──────────────────────────┘
│ · artifact export + Langfuse    │
└─────────────────────────────────┘
```

## Файли

| Файл | Що це |
|------|-------|
| **[`HOW_TO_DEPLOY.md`](./HOW_TO_DEPLOY.md)** | **Основна інструкція** — golden-qa-ds datasource + 6 workers + 4 tools + CI integration + Langfuse + FinOps alerts |
| **[`eval_workflow.yaml.alt-sequential`](./eval_workflow.yaml.alt-sequential)** | **Основний артефакт** — Sequential workflow що iterates Golden Set, scores з 5-dim rubric + RAGAS, виносить CI gate decision |
| [`ws_judge.prompt.md`](./ws_judge.prompt.md) | System prompt для LLM-as-Judge (5-dim rubric + debiasing techniques) |
| [`demo_payloads.md`](./demo_payloads.md) | 5 scenarios: nightly run, case drill-down, verbosity bias test, adversarial, FinOps dashboard |
| [`demo_script.md`](./demo_script.md) | 15-хв сценарій живого показу |

## 6-state eval workflow map

```
    1. load-golden-set
       ↓ (iter_key: cases)

    [For each case у 20 Golden Set — паралельно, max_concurrency=5]
    2. execute-case (trigger prod workflow, get output)
    3. judge-case (5-dim rubric, debiased)
    4. ragas-case (4 math metrics, if RAG-based)
    5. bias-detect (PII leak + prompt injection для adversarial)
       ↓

    6. aggregate-results → CI PASS/BLOCKED decision + artifact JSONL
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

- [ ] W3 workflow `loan-copilot-durable-w3` deployed та проходить smoke test
- [ ] Datasource `golden-qa-ds` з 20 cases (10 eligibility + 5 risk + 3 KYC + 2 adversarial)
- [ ] 6 нових W4 workers створені
- [ ] 4 нових W4 tools у registry (`trigger_production_workflow`, `ragas.compute`, `detect_pii_leak`, `detect_injection_success`)
- [ ] **Judge model з different provider**, ніж agent-under-test (self-enhancement prevention)
- [ ] Eval workflow deployed, slug `loan-copilot-eval-w4`
- [ ] CI integration у GitHub Actions / GitLab (test з dummy PR)
- [ ] Langfuse eval project separate (не змішувати з prod traces)
- [ ] FinOps alerts: 75% utilization → Slack, 95% → PagerDuty
- [ ] Scheduled nightly run cron `0 2 * * *`
- [ ] Backup: VSCode з `python -m module_4_production.src.run_eval`

## 4 pattern'и, які зал має запам'ятати

1. **Eval як окремий workflow, не side-effect prod**. Прод має свій SLO
   (latency < 8s). Eval має свій (cost < $5/run). Different owners.

2. **LLM-as-Judge з debiasing rubric**. Position bias (A/B swap),
   Verbosity bias (-1 clarity for > 500 words), Self-enhancement
   (different provider).

3. **RAGAS != LLM reasoning**. Faithfulness — математична формула
   |verified| / |total|. Deterministic. Calibrated proti human-rated 50.

4. **CI gate = binary decision**. PASS або BLOCKED. 7 criteria все
   true. Нема "approve with warning" — merge блокується якщо хоч одна
   fails.

## Пов'язано

- Домашнє завдання: [`../02-exercise.md`](../02-exercise.md) — Python
  еквівалент eval runner + Langfuse docker setup.
- CI/CD Eval slide у deck: slide #41.
- W3 CodeMie demo (prod workflow що оцінюємо): [`../../module_3_workflows/codemie_demo/`](../../module_3_workflows/codemie_demo/).
- Golden Set JSONL: `../data/golden_qa.jsonl`.
