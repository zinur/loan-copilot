# Module 2 · Grounding, Tools & Enterprise LLM Gateway

> **Webinar 2** · 2 години · 50/50 theory/practice · **AI-архітектор у фінансах**

У цьому модулі агент отримує **очі** (Grounding — як безпечно читати правду зі сховищ банку) та **руки** (Tools — як детерміновано виконувати мутації та зовнішні виклики).

---

## Ключові архітектурні рішення W2

1. **Термінологічний стандарт:**
   - **`CreditHistory`** (інструмент `credit_history.pull`, роль `CreditHistory`, агент `credithistoryagent`) — строго замість застарілих `Bureau` / `BKI`.
   - Інші інструменти: `kyc.lookup` (KYC screening), `risk.score` (scoring computation).

2. **Grounding ≠ RAG:**
   - Векторний RAG застосовується **лише для неструктурованих текстів** (кредитні регламенти, SOP, політики).
   - Для числових розрахунків, балансів та транзакцій — **Semantic Layer / Text-to-SQL** (Cube, dbt, Vanna).
   - Для нормативки НБУ та законів з жорсткою структурою статей — **Hierarchical / PageIndex (ToC-дерево)**.
   - Для стабільних регламентів (< 200K токенів) — **Long-context + Prompt Caching** (знижка 80–90%).
   - Для фінансової математики (ануїтет, PTI, DTI) — **Direct Tools у Python/DuckDB пісочниці** (заборонено рахувати через LLM natural language).
   - Для аналізу бенефіціарів (UBO) та зв'язків PEP — **GraphRAG**.

3. **Advanced Indexing Ladder:**
   - **Hybrid Retrieval:** BM25 (exact IDs, ІПН, номери постанов) + Dense (семантика) з Reciprocal Rank Fusion (RRF).
   - **Contextual Retrieval (Anthropic):** додавання контексту документа до кожного чанка перед ембедінгом.
   - **ColBERT / Late Interaction:** токен-рівневі порівняння (MaxSim) без стиснення в один вектор.
   - **Cross-encoder Reranking:** BGE-reranker / Cohere для відбору top-4 кандидатів з top-25.

4. **Контрольні цикли пошуку (Control Loops):**
   - **Naive RAG (1-Shot):** для публічних FAQ.
   - **Self-RAG & CRAG:** цикли самоперевірки (Document Grading + Faithfulness Verification) — обов'язковий мінімум для кредитних регламентів.
   - **Agentic RAG:** автономне планування та синтез інформації з 3+ систем (PDF регламент + DWH SQL + Core API).

5. **RAG Failure Triage (Методологія діагностики за RAGAS):**
   - **Hallucination (Low Faithfulness):** лікується Citation Gate, примусовим Refusal та temperature=0.
   - **Retrieval Miss (Low Context Recall):** лікується Contextual Chunking, Hybrid BM25 та Reranking.
   - **Factual / Math Error (Low Correctness):** «RAG для правил, Tools для математики».
   - **Intent Drift (Low Relevancy):** лікується Intent Router перед пошуком.

6. **Tool = Контракт & Write-Safety:**
   - 4 гарантії: Pydantic Schema, RBAC у коді бекенду (не в промпті), незмінний Audit Trail, Declared Side-Effect.
   - **Consent Gate:** зовнішні виклики з PII (`credit_history.pull`) фізично вимагають валідного `consent_id` позичальника.
   - **Idempotency Key:** обов'язковий для всіх write-інструментів для захисту від подвійних транзакцій при ретраях.

7. **Enterprise LLM Gateway:**
   - Єдина точка входу (Azure OpenAI EU VPC endpoint або корпоративний DIAL).
   - DLP / PII Masking перед виходом з контуру банку.
   - Tiered Routing: швидкі дешеві моделі ($0.15/1M) для 80% рутини + фронтирні моделі для фінального висновку.
   - Prompt Caching та бюджетні ліміти (100 грн на сесію заявки).

---

## Структура презентації W2 (`deck.html`)

Уся робота над слайдами ведеться у **`deck.html`** (слайди 16–24, повне покриття на 75–88 хв з глибокими speaker notes):

| Слайд | Назва | Архітектурний зміст |
|---|---|---|
| **#16** | `Webinar 2 · agenda` | Рамка модуля: Grounding (очі) + Tools (руки) |
| **#17** | `Grounding menu · 6 базових архітектур` | Мапа вибору: Vector, SQL, PageIndex, Cache, Tools, Graph |
| **#18** | `Advanced RAG · indexing ladder` | 6 інженерних сходинок: Hybrid, Contextual, ColBERT, Rerank |
| **#19** | `RAG control loops` | Naive vs Self-RAG vs Agentic RAG + Decision rule |
| **#20** | `RAG failure triage` | Діагностика 4 аварій: Hallucination, Miss, Math, Drift |
| **#21** | `W2 Theory · write-safety` | 8 правил: контракти, RBAC у коді, consent gate, idempotency |
| **#22** | `Enterprise LLM Gateway` | Шлюз: VPC, DLP, Tiered routing, Prompt cache, circuit breakers |
| **#23** | `W2 Use-case · Eligibility + Risk` | Архітектура Loan Copilot: 40 правил + 3 тули (kyc, credit_history, risk) |
| **#24** | `W2 interactive · Tool contract canvas` | Інтерактивна сесія: проектування 6 полів контракту |
