# Module 3 · Durable workflows · HITL · pause/resume

> **Webinar 3** · 2 години · 50/50 theory/practice · **STUB — наповнюється**

## Що ви навчитесь

- **LangGraph як state engine:** переписати Orchestrator у graph з
  checkpoints. Flow переживає restart.
- **Human-in-the-loop (HITL):** manual review гейт для `decision=="manual_review"`
  та high-value loans (>500K грн).
- **Pause/resume на днях:** клієнт загубив паспорт — flow чекає 7 днів,
  потім продовжує з того ж місця.
- **Compensating actions:** якщо на e-sign щось пішло не так — rollback.

## Прев'ю steps

1. `01-why-durable.md` · моноліт у Python vs state machine з checkpoint
2. `02-langgraph-basics.md` · nodes, edges, state
3. `03-hitl-gate.md` · interrupt → human → resume
4. `04-pause-resume.md` · persistence (SQLite checkpoint)
5. `05-compensation.md` · rollback flows
6. `06-exercise.md` · ваша durable лаба

## Код

Поки порожньо. Файли з'являться після W2.
