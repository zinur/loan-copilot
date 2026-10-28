# Deployment у CodeMie — W3 (Durable Loan Copilot: FSM + HITL + Saga)

> **У W3 Option A (Sequential workflow YAML) стає ОСНОВНИМ**, а не
> alternative. Причина: `assistant_ids` + chat не підтримують durable
> waits (24h/7d), idempotent webhook resume, та per-state retry policies.
>
> Якщо W1/W2 ви робили через sub-assistants pattern — цього тижня
> нарешті копіюємо YAML workflow у CodeMie.

## Що додаємо у W3 vs W2

| Компонент | W2 | W3 |
|---|---|---|
| Routing | LLM parent (handoff) | **Deterministic Sequential FSM** (YAML workflow) |
| Durable waits | ❌ | ✅ 3 waits з `interrupt_before: true` (docs 24h, offer 7d, sign 7d) |
| HITL gates | ⚠️ 1 inline (consent) | ✅ 2 conditional (analyst OCR<0.85, committee grey zone) |
| Pre-write gate | ❌ | ✅ `verify-before-send-agent` (deterministic match) |
| Saga rollback | ❌ | ✅ `compensation-state` (limit.release + contract.cancel + notify) |
| Idempotency | ⚠️ (prompt discipline) | ✅ **Tool-level** `check_idempotency` + composite key |
| Checkpointer | n/a (short-lived) | ✅ **Postgres** (default у CodeMie, survives `kill -9`) |

## Чеклист деплою (~30 хв)

### Крок 1. Перевір prerequisites з W1/W2 (~2 хв)

- [ ] W1 workers існують: `intake-agent`, `underwriter`
- [ ] W2 workers існують: `eligibility-agent`, `risk-agent`
- [ ] W2 tools у registry: `kyc.lookup`, `credit_history.pull`, `risk.score`
- [ ] W2 datasource `loan-policies-ds` indexed

### Крок 2. Створи 4 нових W3 workers (~8 хв)

У CodeMie → **Assistants → + Create Assistant**:

| Slug | Prompt | Model | Temp | Attach |
|---|---|---|---|---|
| `docs-processor-agent` | — simple OCR parser | fast | 0.0 | Tool: `ocr.extract` |
| `contract-draft-agent` | — Jinja template renderer | fast | 0.0 | Tool: `contract.draft`, `limit.reserve` |
| `verify-before-send-agent` | [`ws_verifier.prompt.md`](./ws_verifier.prompt.md) | fast | **0.0** | (none — read-only check) |
| `compensation-agent` | [`ws_compensator.prompt.md`](./ws_compensator.prompt.md) | fast | 0.0 | Tools: `limit.release`, `contract.cancel`, `notify.send` |

### Крок 3. Створи 6 нових W3 tools (~10 хв)

Для production — реальні MCP server / HTTP endpoints. Для демо — додай
inline mocks у system prompts або скористайся WorldcodeMie MCP mock.

| Tool | Side-effect | Idempotent? | Mock location |
|---|---|---|---|
| `ocr.extract(url)` | external-read | Так (same url → same result) | module_3_workflows mock |
| `contract.draft({tax_id, amount, rate, term})` | **internal-write** (reserves limit!) | **Так** (contract_id = f(tax_id, timestamp)) | module_3_workflows mock |
| `limit.reserve(contract_id, amount)` | **internal-write** | Так (composite key) | module_3_workflows mock |
| `limit.release(contract_id)` | internal-write (rollback) | Так | module_3_workflows mock |
| `contract.cancel(contract_id)` | internal-write (rollback) | Так | module_3_workflows mock |
| `notify.send(tax_id, template, channel)` | external-write (SMS/email) | Так (deduplication key) | module_3_workflows mock |

### Крок 4. Checkpointer — nothing to configure (CodeMie-managed)

У CodeMie checkpointer **hardcoded** на платформі
(`src/codemie/workflows/checkpoint_saver.py:CheckpointSaver` →
Postgres через `WorkflowExecution > WorkflowExecutionCheckpoint`
data model). **Жодного Admin UI toggle немає** — усі workflows з
`interrupt_before: true` автоматично persistent.

**Що треба перевірити (deployment-level, не tenant UI):**
- CodeMie backend DB = Postgres (не SQLite dev-mode) → якщо PB хостить
  on-prem, це SRE концерн, не SA task.
- Retention policy на `workflow_execution_checkpoints` table —
  налаштовує DBA (90 днів для NBU audit).

**Що робити якщо `kill -9` test проваливається:**
- Перевір, що CodeMie запущений з `postgres://` connection string,
  не з `sqlite://` або in-memory (`:memory:`) config.
- Логи pod'а: шукай рядок `CheckpointSaver initialized with Postgres`.

### Крок 5. Deploy workflow YAML (~3 хв)

У **Workflows → + Create Workflow**:
1. Switch View → **YAML**.
2. Copy-paste [`durable_copilot_workflow.yaml.alt-sequential`](./durable_copilot_workflow.yaml.alt-sequential).
3. Save → перевір validation OK.
4. Workflow slug = `loan-copilot-durable-w3`.

### Крок 6. Налаштуй webhook endpoints (~2 хв)

У **Admin → Integrations → Webhooks** → додай:

| Event name | Endpoint URL | Signature verification |
|---|---|---|
| `docs.uploaded` | `/api/v1/workflows/.../resume` | HMAC-SHA256 з Privat24 shared secret |
| `offer.decision` | `/api/v1/workflows/.../resume` | HMAC-SHA256 |
| `contract.signed` | `/api/v1/workflows/.../resume` | **Signature від Дія.Підпис** (JWT) |

Для демо signature verification можна temporarily disable, але у prod — mandatory.

### Крок 7. Set workflow-level timeouts (~1 хв)

У workflow edit → Execution config:

```yaml
max_wait_duration:
  docs-requested-state: 172800  # 48h in seconds
  analyst-review-state: 7200    # 2h for operator SLA
  committee-review-state: 86400 # 24h for committee
  offer-state: 604800           # 7d
  sign-awaiting-state: 604800   # 7d
```

Якщо timeout — автоматично transitions у `compensation-state`.

### Крок 8. Smoke test (~5 хв)

Запусти Scenario #1 з [`demo_payloads.md`](./demo_payloads.md) → має пройти
intake → pause на docs-requested → curl resume → OCR → kyc → credit →
underwriter → contract → verify → pause на offer → curl resume → pause
на sign → curl resume → disbursed.

**Якщо падає на kill -9 test:** CodeMie backend running on in-memory
DB замість Postgres — deployment issue (SRE). Нічого не fix'иш з
tenant UI, тільки через re-deploy платформи з Postgres connection.

---

## Pre-stage checklist (перед live demo)

- [ ] 4 нових W3 workers створені та прикріплені tools
- [ ] 6 нових W3 tools у registry з mock responses
- [ ] CodeMie backend на Postgres (checkpointer автоматично persistent)
- [ ] Workflow YAML завантажений, slug `loan-copilot-durable-w3`
- [ ] 3 webhook endpoints зареєстровані
- [ ] Workflow timeouts налаштовані
- [ ] Terminal готовий з curl commands (для resume)
- [ ] k9s / kubectl setup для `kill -9` демонстрації
- [ ] Admin → Workflows → Executions tab відкрита (live view)
- [ ] Backup: VSCode з `python -m module_3_workflows.src.workflow_engine`
      якщо CodeMie workflow failing

## Поширені проблеми

**"Workflow не паузиться на `interrupt_before`"** → перевір Admin
→ Workflows → Advanced → "auto-resume on interrupt" має бути **OFF**.

**"`kill -9` pod'а → workflow lost"** → CodeMie backend на in-memory
DB замість Postgres. Deployment issue — tenant UI це не fix'ить.
Перевір SRE: `CheckpointSaver` має bind до Postgres connection pool.

**"Resume webhook повертає 404"** → thread_id у URL неправильний.
Візьми з Executions → копіюй повний execution_id, не workflow_id.

**"Idempotency не працює, disburse двічі"** → tool `check_idempotency`
не attached або mock implementation не перевіряє dict. Spot-check:
Admin → Tools → `check_idempotency` → test call.

**"Saga compensation не запускається"** → перевір condition у
`verify-before-send-state.next` → `otherwise: compensation-state`.
У YAML легко описатися.

## Міграційний шлях W2 → W3

Якщо у вас production з W2 architecture і хочете мігрувати на W3:

1. **Phase 1 (ризик 0):** Deploy workflow паралельно, A/B test на 5% traffic.
2. **Phase 2:** Миграція existing W2 sessions — експортуй їхні state,
   імпортуй як resumable executions (checkpoint rows у Postgres).
3. **Phase 3:** W2 deprecated, усі нові заявки через W3 workflow.

CodeMie підтримує паралельні workflow versions, legacy W2 flow може
працювати ще 90 днів для in-flight заявок.
