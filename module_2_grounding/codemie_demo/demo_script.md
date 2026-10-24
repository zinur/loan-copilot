# W2 live demo script · ~10 хв у CodeMie

> Контекст: W1 вже показали, зал бачив mocked copilot з 4 agents.
> Сьогодні додаємо grounding + tools + gateway. Головний message:
> "LLM більше не вигадує політики банку — усе з обов'язковою цитатою."

## Хронометраж

| Хв | Що показуємо | CodeMie вкладка |
|---|---|---|
| 0:00–1:00 | Що змінилося vs W1 (швидкий diff) | Assistants list |
| 1:00–2:00 | Datasource `loan-policies-ds` (RAG index) | Datasources |
| 2:00–3:00 | eligibility-agent config (Description + attached DS) | Assistant edit |
| 3:00–5:00 | **Payload #1 Happy path** (повний flow) | Parent chat |
| 5:00–6:00 | Trace у Langfuse (tool_calls_made + citations) | Langfuse tab |
| 6:00–7:30 | **Payload #2 FOP refusal** (RAG cite POL-LOAN-01 §4.2.1) | Parent chat |
| 7:30–9:00 | **Payload #3 Consent block** (interrupt_before works) | Parent chat |
| 9:00–10:00 | Guardrails config walk-through + Q&A | Project → Guardrails |

---

## 0:00–1:00 · Diff з W1

**Що говориш:**
> "Минулого тижня був mocked copilot — 4 агенти, lookup таблиці у system
> prompt. Сьогодні замінюємо mock-таблиці на реальні джерела: RAG на
> банківських регламентах + 3 банківські tools + LLM Gateway з PII
> masking. Один артефакт — наш `loan-copilot` repo, module_2 додається
> ПОВЕРХ module_1."

**Клік:** Assistants list → scroll через 6 workers → підкресли нові
`eligibility-agent` та `risk-agent` з іконками "RAG" і "Tools".

---

## 1:00–2:00 · Datasource preview

**Клік:** Datasources → `loan-policies-ds` → **Test query** input:
> `"ФОП 4 місяці consumer loan"`

**Очікуване видання:**
- Top-1: `POL-LOAN-01.md` чанк §4.2.1 (**цей**).
- Top-2–4: інші POL/SOP chunks з лексичним match.

**Що говориш:**
> "Hybrid search — BM25 для exact-match («§4.2.1», «ФОП») + dense для
> семантики («реєстрація 4 місяці» = «stage < 6 міс»). Reranker піднімає
> cross-encoder-ом конкретне правило винятку на #1. Без reranker воно
> було б на #7 — за загальним описом політики кредитування."

---

## 2:00–3:00 · Eligibility assistant edit

**Клік:** Assistants → `eligibility-agent` → Edit.

**Показуємо:**
- **System prompt** — scroll до "RAG retrieval discipline" секції.
  Акцент на "**НЕ генеруй відповідь**, якщо RAG не знайшов" + examples
  refusal patterns.
- **Attached datasource:** `loan-policies-ds` ← якщо не прикріплено,
  агент працювати не буде.
- **Temperature: 0.0** ← grounded answers must be deterministic.
- **Tools: 0** ← eligibility НЕ викликає tools, лише RAG.

**Питання до залу:** "Чому `eligibility` окремо від `risk`? Чому не
один агент?" → **Правильна відповідь:** different data access (RAG vs
tool), different RBAC, different SLO (RAG ~200ms, tool ~800ms), different
owners (Policy team vs Risk team).

---

## 3:00–5:00 · Payload #1 Happy path

**Копіюй** payload #1 з [`demo_payloads.md`](./demo_payloads.md) у parent chat.

**Що покроково бачать:**

1. **Intake** — повертає JSON з 8 полями.
2. **Eligibility** — JSON з `citations: ["[POL-LOAN-01 §2.1]", ...]`.
   **Клік у citation у UI** → показує уривок чанку з datasource.
3. **Consent Gate** — chat PAUSES. UI показує "waiting for human
   confirmation". Click **Resume with input** → введи `{"consent_given": true, "consent_id": "consent-demo-01"}`.
4. **KYC → Credit History → Risk** — показує tool_calls_made у output.
5. **Underwriter** — `decision: approve, rate_apr: 18.5` з citation
   `[SOP-CREDIT-02 §5]`.

**Що говориш у момент consent gate:**
> "Ось це головне у W2. Workflow паузиться, бо `interrupt_before: true`
> на цьому state. У проді замість чат-prompt'у — це операційник у
> back-office UI натискає «згода отримана» після того як клієнт
> підписав OTP. CodeMie сам підхопить і продовжить з того ж thread_id."

---

## 5:00–6:00 · Langfuse trace

**Клік:** Open Langfuse → знайти trace щойно виконаної сесії.

**Показуємо trace tree:**
```
[trace: lc-2026-10-24-abc]
├── intake-state       (180ms, 1200 tokens)
├── eligibility-state  (420ms, RAG retrieval + LLM)
│   └── rag.search     (42ms, 4 chunks returned)
├── consent-gate       (interrupted, resumed after 2min human delay)
├── kyc-state
│   └── tool.kyc_lookup (320ms, mock response)
├── credit-history-state
│   └── tool.credit_history_pull (820ms, consent_id verified)
├── risk-state
│   └── tool.risk_score (120ms, band=A)
└── underwriter-state  (640ms, 2200 tokens, Tier 2 LLM)
```

**Що говориш:**
> "Кожен span з `gen_ai.*` attributes (OpenTelemetry GenAI semconv) +
> наші додаткові: `tool.name`, `rbac.role`, `consent_id_present`,
> `cost_usd`. Audit log для НБУ готовий — 7 років retention."

---

## 6:00–7:30 · Payload #2 FOP refusal

**Копіюй** payload #2 → "Петро Шевченко, ФОП 4 місяці..."

**Що бачать:**
1. Intake OK.
2. **Eligibility — refusal with citation:**
   ```json
   {
     "eligible": false,
     "citations": ["[POL-LOAN-01 §4.2.1]"],
     "reason": "ФОП < 6 міс → consumer недоступний. Альтернатива: забезпечувальний депозит (SOP-CREDIT-02 §7.3).",
     "escalate_to_human": false
   }
   ```
3. **Workflow термінує.** Нема KYC, нема UBKI, нема Underwriter.

**Акцент:**
> "Air Canada case won't happen у нашого копілота. Клієнт отримав
> **точну причину з посиланням на пункт регламенту** замість вигаданого
> тексту. Юрист банку може взяти цей output і довести у суді, що
> рішення обґрунтоване.
>
> Бонус для CFO: ми НЕ викликали UBKI ($1.50 saved) ← при 7000 refusals
> на день це ~$10.5k saved/day."

---

## 7:30–9:00 · Payload #3 Consent block

**Копіюй** payload #3 (Олена Приходько) + при consent prompt відповідаєш
"не хочу давати згоду".

**Що бачать:**
1. Intake OK.
2. Eligibility OK.
3. **Consent Gate — pause.**
4. Клієнт відмовляє → `consent_given: false, consent_id: ""`.
5. Workflow термінує на consent gate, НЕ викликає UBKI.
6. **Audit log entry:** `BLOCKED_NO_CONSENT`.

**Акцент:**
> "Два рівні захисту: (1) prompt discipline — risk-agent не викличе
> tool без consent_id, (2) tool-level enforcement — сам Python wrapper
> credit_history_pull перевіряє consent у БД.
>
> **Чому обидва?** Air Canada навчила, що prompt-only захист ламається
> prompt-injection'ом. Deterministic tool-level — second line of defense."

---

## 9:00–10:00 · Guardrails walkthrough + Q&A

**Клік:** Project → Guardrails.

**Показуємо:**
- **DLP policy:** PAN Luhn, IBAN UA, ІПН patterns.
- **Input filter:** "Override policy" patterns з Chevrolet case.
- **Output filter:** price commitment без source.

**Питання до залу (на debrief):**
1. "Що зламається, якщо прибрати `eligibility-agent` і дати цю задачу
   underwriter'у?" → Відповідь: no citation discipline, галюцинації
   політик, Air Canada risk, +~$1.50/лід marnie тратимо.

2. "Чому `consent-gate` окремий state, а не параметр tool'у?" →
   Відповідь: durable pause дозволяє чекати дні, якщо клієнт пішов
   думати. У W3 розкриємо durability.

---

## Переходи у наступний тиждень (W3)

> "Домашнє на тиждень: `module_2_grounding/03-exercise.md` — vibe-code
> OK, реалізуйте Consent Gate + Citation discipline у Python репо.
>
> Наступного тижня — W3 — ми візьмемо цей workflow і зробимо його
> **durable**: виживає рестарт K8s, чекає клієнта днями, не дублює
> виплати. Loan Copilot stops being a demo і стає банківським workflow."
