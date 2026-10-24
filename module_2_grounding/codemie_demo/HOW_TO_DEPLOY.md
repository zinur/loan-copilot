# Deployment у CodeMie — W2 (Grounded Loan Copilot: RAG + Tools + Gateway)

> **Основний шлях:** додаємо 2 нових worker-assistants (`eligibility-agent`,
> `risk-agent`) до parent-а з W1. Жодного YAML workflow не треба — parent
> з `assistant_ids` автоматично розширюється.
>
> Альтернатива (deterministic Sequential workflow з HITL interrupt на
> consent) — у [`grounded_copilot_workflow.yaml.alt-sequential`](./grounded_copilot_workflow.yaml.alt-sequential).

## Що додаємо у W2 vs W1

| Compoment | W1 state | W2 state |
|---|---|---|
| Eligibility | ❌ не було | ✅ новий `eligibility-agent` з RAG на `loan-policies-ds` |
| KYC | ✅ (mock lookup table) | ✅ **тепер real tool** `kyc.lookup` |
| Consent Gate | ❌ не було | ✅ **`interrupt_before: true`** перед UBKI |
| Credit History | ✅ (mock lookup table) | ✅ **real tool** `credit_history.pull` з consent_id |
| Risk Score | ❌ не було | ✅ новий `risk-agent` з tool `risk.score` |
| LLM Gateway | ⚠️ default | ✅ DIAL on-prem або Azure OpenAI EU (PII masking + budgets) |

## Чеклист деплою (~25 хв)

### Крок 1. Залий datasource `loan-policies-ds` (~5 хв)

У CodeMie → **Datasources → + Create Datasource**:

| Поле | Значення |
|------|----------|
| Name | `loan-policies-ds` |
| Type | Markdown collection (hybrid: BM25 + dense) |
| Chunk size | 500 tokens |
| Chunk overlap | 50 tokens |
| Reranker | BGE-reranker (if available) або Cohere Rerank |
| Metadata filter | `effective_date` (datetime) |

**Upload:** 3 файли з `module_2_grounding/data/policies/`:
- `POL-LOAN-01.md` (політика кредитування, ФОП §4.2.1)
- `SOP-CREDIT-02.md` (ануїтет, PTI, 4-tier matrix §5)
- `REG-NBU-351.md` (постанова НБУ, валютні обмеження)

Перевір індексацію: Datasource → Test query → `"ФОП менше 6 місяців"` →
має повернути `POL-LOAN-01 §4.2.1` на позиції Top-1 після reranker.

### Крок 2. Створи 2 нових worker-assistants (~5 хв)

| Assistant slug | System prompt звідки | Model | Temperature | Attach |
|---|---|---|---|---|
| `eligibility-agent` | [`ws_eligibility.prompt.md`](./ws_eligibility.prompt.md) | fast-tier | 0.0 | Datasource: `loan-policies-ds` |
| `risk-agent` | [`ws_risk.prompt.md`](./ws_risk.prompt.md) | fast-tier | 0.0 | Tools: `kyc.lookup`, `credit_history.pull`, `risk.score` |

**Для `eligibility-agent`:**
- Attach datasource `loan-policies-ds` ← Критично, без цього RAG не працює.
- У Description коротко: *"Перевіряє eligibility клієнта за внутрішніми
  регламентами, завжди цитує § SOP/POL/REG."*

**Для `risk-agent`:**
- Attach 3 tools (створи у Крок 3 нижче, потім повернись сюди).
- Description: *"KYC + UBKI pull + risk-band scoring. Consent Gate enforcement."*

### Крок 3. Створи 3 tools (~10 хв)

Для production — MCP server або HTTP integration. Для workshop — inline
mock responses у system prompt (швидше).

**Варіант A (швидкий, для демо): Inline mock у system prompt.**

Додай у [`ws_risk.prompt.md`](./ws_risk.prompt.md) після `## Контракт на tool calls`:

```markdown
## Mock lookup tables (для workshop)

**kyc.lookup:**
- `1234567890` → `{passed: true, sanctions: false, pep: false}`
- `9876543210` → `{passed: true, sanctions: false, pep: false}`
- `5555555555` → `{passed: false, sanctions: true, pep: false}`
- default → `{passed: true, sanctions: false, pep: false}`

**credit_history.pull:**
- `1234567890` → `{score: 720, arrears: []}`
- `9876543210` → `{score: 580, arrears: ["LN-2024-0042"]}`
- `5555555555` → `{score: 650, arrears: []}`
- default → `{score: 650, arrears: []}`

**risk.score:** compute локально за правилом:
band = "A" if bureau_score >= 700 and pti <= 0.35 else
       "B" if bureau_score >= 620 and pti <= 0.45 else
       "C" if bureau_score >= 550 and pti <= 0.50 else "D"
```

Agent "викликає" tool у текстовому форматі (без реального tool call) —
працює для demo, не для production.

**Варіант B (для production prep): реальні tools у CodeMie Tools registry.**

У CodeMie → **Tools → + Create Tool** × 3:

Для кожного візьми OpenAI function schema з
[`tool_contracts.yaml.alt`](./tool_contracts.yaml.alt) та prikкріпи до
`risk-agent` через Assistant → Tools multi-select.

### Крок 4. Налаштуй LLM Gateway (~5 хв)

У **Admin → AI Models** → перевір, що:

- Default provider = DIAL on-prem (або Azure OpenAI EU private endpoint).
- US-based OpenAI, Anthropic прямо — **заборонено** (banking secret Ст. 60).

У **Project → Guardrails** → увімкни:

- **DLP:** patterns з [`gateway_config.yaml.alt`](./gateway_config.yaml.alt)
  (PAN Luhn, IBAN UA, phone +380, ІПН).
- **Input filter:** prompt injection detection.
- **Output filter:** price commitment без source, toxic patterns.

У **Admin → Billing** → budget caps:
- Per user per day: 100 UAH.
- Per project per day: $500 hard-stop.

### Крок 5. Онови parent-assistant (`loan-copilot-orchestrator`) (~3 хв)

У parent → **Sub-assistants** → додай нові:
- `eligibility-agent`
- `risk-agent`

Онови system prompt (додай у existing [`ws_orchestrator.prompt.md`](../../module_1_agents/codemie_demo/ws_orchestrator.prompt.md)):

```markdown
## Нові workers (W2)

- **eligibility-agent** — перевіряє чи клієнт у принципі може подати
  заявку. Викликай ПЕРЕД kycagent. Якщо повертає `eligible=false` —
  зупинка, повідом клієнту причину з цитатою.
- **risk-agent** — замість старих `kycagent` + `credithistoryagent` окремо.
  Викликай ПІСЛЯ eligibility АЛЕ лише після того, як отримав `consent_id`
  від клієнта (питай клієнта explicit: "Згодні на запит УБКІ?").
```

### Крок 6. Smoke test (~2 хв)

Запусти payload #1 з [`demo_payloads.md`](./demo_payloads.md) → має пройти
intake → eligibility (cite POL-LOAN-01) → consent gate (pause) → KYC →
UBKI → risk → underwriter → approve.

**Якщо падає:**
- No citation у eligibility → datasource не indexed, перевір Datasource preview.
- Tool call fails → перевір RBAC (role на assistant має match tool contract).
- Workflow крутиться на consent gate → це нормально, interrupt_before works.

---

## Pre-stage checklist (перед live demo)

- [ ] Datasource `loan-policies-ds` з 3 файлами, test query працює.
- [ ] 2 нових workers (`eligibility-agent`, `risk-agent`) створені,
      system prompts залиті з repo.
- [ ] 3 tools у registry (або inline mocks у prompt).
- [ ] LLM Gateway вказує на DIAL/Azure EU, НЕ на public OpenAI.
- [ ] Guardrails (DLP, Input/Output filter) увімкнені на project.
- [ ] Budget caps налаштовані.
- [ ] Parent-assistant має 6 sub-assistants (4 з W1 + 2 нових).
- [ ] Open у 3 вкладках: parent chat, Datasource preview, Langfuse trace.
- [ ] Backup: VS Code з `python -m module_2_grounding.src.orchestrator_v2` готовий.

## Поширені проблеми

**"Eligibility agent не цитує § з документів"** → datasource не attached
на assistant level, або attached але Chunking виставлено занадто велике
(1000+) — не влізе у context. Spot-check: chunk size має бути 500 tok.

**"Interrupt_before на consent gate не паузить"** → перевір, що у
assistant settings вимкнено "auto-resume on interrupt". Default — pause.

**"Credit history pull повертає score з голови"** → tool не attached,
agent галюцинує. Attach у Assistant → Tools, перевір schema.

**"PII витікає у Langfuse traces"** → DLP policy на Project level
не enabled, або PII patterns не покривають твій формат (напр., ІПН 10-цифр
може конфліктувати з amount 1500000 — додай context у pattern).
