# Deployment у CodeMie 2.59.0 — крок за кроком

Базовано на [docs.codemie.ai](https://docs.codemie.ai/user-guide/workflows/).
CodeMie вміє native workflow з Visual Editor **і** YAML (вони синхронні).

## Два режими assistant'ів у workflow

CodeMie workflow YAML дає **2 способи** підключити LLM-агента до state:

```yaml
# Режим 1 — REFERENCE на вже створеного assistant'а (рекомендовано):
assistants:
  - id: intake                     # локальний alias всередині workflow
    assistant_id: ws-intake        # slug вже існуючого CodeMie Assistant

# Режим 2 — INLINE (все прямо у workflow):
assistants:
  - id: intake
    model: gpt-4.1
    system_prompt: |
      Ти Intake-спеціаліст…
    tools:
      - name: mock_kyc_check
```

| | Reference (`assistant_id:`) | Inline |
|---|---|---|
| Де живе prompt | У окремому assistant'і (CodeMie Assistant Builder) | У YAML workflow |
| Reuse поза workflow | ✅ Ассистент можна відкрити в chat окремо | ❌ Тільки в workflow |
| Перенести між тенантами | Потрібно створити assistant'ів заздалегідь | ✅ Один файл — save |
| Зміни prompt | У UI Assistant'а (версійний контроль) | git-friendly (prompt в YAML) |
| Для воркшопу | Демонструє «два рівні» (prompt + routing) — краще | Простіше, деплой в 1 клік |

**Для live-демо беремо Reference-режим** — на сцені клікаєш по assistant'у
в UI, відкриваєш system prompt, показуєш «scope isolation» — потім переходиш
в workflow і показуєш routing. Два рівні абстракції — сильніше враження.

Якщо CodeMie 2.59.0 не підтримує `assistant_id:` reference — в нашому YAML є
**варіант B (в кінці файлу, закоментований)**, який додає inline prompts.

## Чому Workflow, а не sub-assistants

CodeMie має **два механізми** для multi-agent:

| Механізм | Де править routing | Audit | Коли брати |
|----------|---------------------|-------|------------|
| **Workflow** (Visual + YAML) | Conditional/Switch nodes (детерміновано) | Node-by-node execution log | Процеси з політикою (loan, compliance) ✅ наш кейс |
| **Sub-assistants** (orchestrator-assistant delegates to specialists) | LLM у orchestrator'а | Chat history | Research, open-ended tasks |

Для consumer-loan ми беремо **Workflow** — routing має бути детермінованим
(НБУ-audit), а не на LLM-reasoning. Supervisor = сам workflow engine.

## Чеклист деплою (~20 хв)

### Крок 1. Створи 4 assistants (для Reference-режиму)

> Якщо береш **inline-варіант B** — пропускай цей крок, workflow сам
> створить assistant'ів внутрішньо.

У CodeMie → **Assistants → + Create Assistant**. Для кожного:

| Assistant slug | System prompt звідки | Tools |
|----------------|----------------------|-------|
| `ws-intake` | `ws_intake.prompt.md` | — |
| `ws-kyc` | `ws_kyc.prompt.md` | `mock_kyc_check` (MCP або built-in) |
| `ws-credit-history` | `ws_credit_history.prompt.md` | `mock_credit_history` |
| `ws-underwriter` | `ws_underwriter.prompt.md` | — |

**Settings для кожного:**
- Model: `gpt-4.1` (або що є у вашому EPAM тенанті)
- Temperature: `0.1` для intake/kyc/credit, `0.0` для underwriter
- Shared with Project: ON (щоб workflow міг на них посилатись)

### Крок 2. (Якщо MCP tools недоступні) — моки через `mock_responses` у prompt

Варіант A — у вас є MCP tool server, прикріпи його до assistant.

Варіант B (простіше для демо) — вшити mock в system prompt KYC/Credit:

```markdown
# Додай у кінець ws_kyc.prompt.md:

## Mock data (для workshop demo, НЕ production)

Замість реального tool call використовуй цю таблицю:
| tax_id       | passed | sanctions | pep   |
|--------------|--------|-----------|-------|
| 1234567890   | true   | false     | false |
| 9876543210   | true   | false     | false |
| 5555555555   | false  | true      | false |
| (інше)       | true   | false     | false |
```

Аналогічно для credit_history (720, 430, 650).

### Крок 3. Створи Workflow

**Workflows → + Create Workflow → перемкни View у YAML** → встав
`loan_copilot_workflow.yaml` → **Save**.

Якщо CodeMie не впізнає якийсь ключ (версія 2.59.0 може відрізнятись):
- `custom_node_id: transform_node` — у Visual Editor це **Transform Node**.
  Якщо ім'я типа інше у вашій версії — заміни. Поведінка та сама.
- `next.condition` з `expression / then / otherwise` — це Conditional Node.
- `next.switch` — Switch Node (у нашому YAML не вжито, тільки conditional).
- `interrupt_before: true` — HITL pause.

### Крок 4. Visual review

Після save перемкни на **Visual** вкладку. Маєш побачити граф:

```
Start → intake → check-intake ─┬─(ok)──→ kyc → check-kyc ─┬─(pass)──→ credit-history
                                │                          │
                                └─(incomplete)             └─(fail)──→ decline-kyc → End
                                      ↓                                    ↓
                                   End                                    End

                      credit-history → check-score ─┬─(ok)──→ underwriter → End
                                                    │
                                                    └─(low)──→ decline-score → End
```

Якщо нема — Beautify button.

### Крок 5. Testing

**Save & Run** → у поле executioninput встав:

```json
{
  "message": "ПІБ Марія Коваленко, ІПН 1234567890, дохід 35000, сума 150000, строк 24, мета consumer"
}
```

Очікуваний результат: `decision=approve, rate_apr=18.5%`.

Якщо `ws-intake` не парсить natural language у поля — додай у його
system prompt приклад input/output; або прийми alternative — передавай
від початку structured JSON:

```json
{
  "full_name": "Марія Коваленко",
  "tax_id": "1234567890",
  "monthly_income": 35000,
  "amount": 150000,
  "term_months": 24,
  "purpose": "consumer",
  "phone": "+380501234567",
  "email": "maria@example.com"
}
```

Структурований JSON автоматично потрапляє у context store, і Intake
може одразу повернути `intake_complete=true`.

## Під час live demo — що показувати

1. **Visual tab** — малюєш діаграму на екрані, аудиторія бачить supervisor
   pattern візуально. Клікни по `check-score` node → показуєш правило
   `credit_score >= 500` — ось де вся policy.
2. **Assistant tab для ws-kyc** → scroll до «Що ти НЕ робиш» → акцент
3. **Execution** → ввести Payload 1 → watch trace → open node details
   (клікни на кожну node щоб побачити input/output JSON)
4. **Повторити з Payload 2** (bad tax_id) → check-score fail → decline-score
5. **(bonus)** interrupt_before HITL → увімкни у underwriter → при
   запуску workflow зупиниться і попросить approve → саме що треба banking

## Troubleshooting

| Симптом | Причина | Fix |
|---------|---------|-----|
| `assistant_id ws-kyc not found` | assistant створено але не shared | Settings → Shared with Project ON |
| Transform node not available | version 2.59.0 має інший ім'я | Відкрий Visual → + Node → знайди "Transform"/"Data"/"Custom" у palette |
| Jinja `{{credit_score}}` рендериться як literal | field не в context_store | Переконайся `store_in_context: true` на попередньому state |
| LLM повертає plain text замість JSON | Відсутня / неправильна `output_schema` | Додай output_schema з required fields, model fallback на JSON mode |
| Conditional завжди ітлюче else | Expression syntax різний у 2.59.0 | Спробуй `"{{credit_score}} >= 500"` замість `"credit_score >= 500"` |

## Reference

- Workflow states types: https://docs.codemie.ai/user-guide/workflows/configuration/workflow-states/
- State transitions (Conditional/Switch): https://docs.codemie.ai/user-guide/workflows/configuration/state-transitions/
- Context management: https://docs.codemie.ai/user-guide/workflows/configuration/context-management/
- Examples (включно з Transform Node for routing): https://docs.codemie.ai/user-guide/workflows/configuration/examples/
- HITL (`interrupt_before`): той самий examples page, 11.5
