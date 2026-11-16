# W4 live demo · ~15 хв · код → CodeMie workflow → Langfuse

> Головний message: «Якість міряється кодом, а не відчуттям. Той самий golden set
> можна прогнати локально й через реальний workflow на платформі — і побачити результат в одному місці.»
> Мета — показати процес, а не 100% ідеальні метрики.

## Підготовка (до початку, 5 хв)

```bash
cd loan-copilot
docker compose -f module_4_production/docker/docker-compose.yml up -d     # Langfuse v2 → http://localhost:3000
export CODEMIE_API_URL=https://codemie.lab.epam.com/code-assistant-api
export CODEMIE_WORKFLOW_ID=<id workflow loan-copilot-policy-qa>             # з URL сторінки workflow
export CODEMIE_COOKIE='_oauth2_proxy=<значення зі свіжого логіну>'          # живе кілька годин
export LANGFUSE_PUBLIC_KEY=pk-lf-...  LANGFUSE_SECRET_KEY=sk-lf-...
.venv/bin/python -m module_4_production.src.run_eval --mode workflow --limit 1   # smoke-перевірка
```

Workflow `policy_qa_workflow.yaml` і асистент `eligibility-agent` (datasource `loan-policies-ds`) уже задеплоєні — на демо нічого не створюємо.
Запасний варіант, якщо стенд недоступний: тільки частини 1 і 3 (режим fixtures працює без CodeMie).

## Хронометраж

| Хв | Що показуємо | Де |
|---|---|---|
| 0:00–1:00 | Навіщо eval: «відчуття» → вимір | слайд 122 |
| 1:00–4:00 | Частина 1 · три відповіді на QA-004 + Quality Gate | термінал |
| 4:00–9:00 | Частина 2 · golden set через CodeMie workflow | термінал + CodeMie UI |
| 9:00–13:00 | Частина 3 · трейси й оцінки в Langfuse | браузер |
| 13:00–15:00 | Розбір FAIL, обмеження, Q&A | термінал |

## Частина 1 · код: три відповіді (1:00–4:00)

```bash
.venv/bin/python -m module_4_production.src.run_eval
```

Показуємо: реально знайдений контекст (W2 пошук), три відповіді-фікстури, Faithfulness рахує код.
A проходить; B (красива вода) заблоковано — немає цитати §4.2.1; C (галюцинація) заблоковано. Код виходу 1.

> «Відповіді тут — фікстури, але метрики й рішення гейту — справжній код. Суддя-LLM у коді не реалізований.»

## Частина 2 · workflow на платформі (4:00–9:00)

1. CodeMie UI → Workflows → `loan-copilot-policy-qa` → YAML: один вузол, `output_schema` з полем `decision` (approve / reject / hitl_escalate / refuse_injection).
   > «Це код-перший підхід: контракт відповіді задаємо схемою, а не сподіваємось на текст.»
2. Термінал:
   ```bash
   .venv/bin/python -m module_4_production.src.run_eval --mode workflow --limit 3
   ```
   Для кожного кейсу видно: питання → статус виконання → відповідь workflow → перевірка кодом (вердикт, цитата, PII, ін'єкція).
3. Повний прогон `--mode workflow` (20 кейсів, кілька хвилин; можна запустити заздалегідь і показати готовий результат).

Gate: точність вердикту ≥ 92% (19/20) і жодного PII чи вдалої ін'єкції.

> Golden set звірений із корпусом політик: 9 з 20 питань навмисно поза корпусом — правильна відповідь «ескалація», а не вигаданий пункт.

## Частина 3 · Langfuse (9:00–13:00)

Браузер → http://localhost:3000 → проєкт `loan-copilot`:
- Tracing → Traces: `golden/QA-xxx` — питання, відповідь, вкладка Scores.
- Tracing → Sessions: сесія = назва запуску (`run_YYYYMMDD_HHMMSS`).
- Dashboard: сумарні оцінки по запуску.

> Чесно скажіть: це трейси eval-раннера. `faithfulness` у режимі workflow рахується проти нашого локального контексту, а не проти чанків CodeMie — це індикатор, а не гейт.

## Розбір FAIL і Q&A (13:00–15:00)

Відкрийте кейс, де вердикт не збігся: що відповів workflow, що очікував golden set, хто неправий — агент чи розмітка.
Типова відповідь: розмітка (вигаданий пункт) → виправляємо golden set, а не промпт.

**Q: Де CI/CD?** Поза скоупом воркшопу: раннер повертає код виходу, його можна підключити до будь-якого CI.
**Q: Чому не LLM-суддя?** У коді він не реалізований; у CodeMie його можна додати окремим вузлом (див. `eval_workflow.yaml.alt-*`, довідково).
**Q: FinOps?** Модельні припущення, у практику не входить (див. слайди FinOps).

## Домашнє

`module_4_production/02-exercise.md`: те саме, що ми показали, але локально, без CodeMie: свій Python-бекенд (`--mode local`), Langfuse у docker, golden set як Dataset, тести й результати в Langfuse.
