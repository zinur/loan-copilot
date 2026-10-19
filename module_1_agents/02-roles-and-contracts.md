# 02 · 7 ролей та I/O контракти

## Learning objective

Визначити 7 specialist agent'ів Loan Copilot. Побачити, як Pydantic
schemas формують контракти між ними.

## Prerequisites

[01-why-split.md](./01-why-split.md)

## Narrative

Коли ми прийняли рішення розділити моноліт, виникає питання: **на що саме
розділити?** Є кілька помилкових підходів:

- *По функціях LLM:* «Summarizer agent», «Classifier agent», «Writer
  agent». Це технічний поділ, що не має сенсу для бізнесу. Compliance
  не вміє читати такі діаграми.
- *По tools:* «CreditHistoryCallerAgent», «KYCCallerAgent». Проблема — ролі
  плутаються з інфраструктурою. Якщо завтра зміните vendor credit-history,
  доведеться перейменовувати agent'а.
- *По business role.* Так робить DBS, Klarna, і будь-який банк, що
  прожив > 2 роки у production. Ролі збігаються з job description у
  вашому back-office.

Для Loan Copilot беремо **7 ролей**, що мапуються на реальні функції
кредитного департаменту банку:

| # | Agent | Відповідальність | Analog у back-office |
|---|-------|------------------|----------------------|
| 1 | **Intake** | Збір заявки, валідація полів | Кредитний менеджер (front) |
| 2 | **KYC** | Identity check, sanctions, PEP | Compliance officer |
| 3 | **CreditHistory** | Credit history pull та нормалізація | Credit analyst (data) |
| 4 | **Underwriter** | Policy check, PTI calc, risk tier | Underwriter |
| 5 | **Pricing** | Visualization ставки за tier | Pricing manager |
| 6 | **Contract** | Генерація контракту за template | Loan ops |
| 7 | **Compliance** | Final review перед e-sign | Compliance (sign-off) |

**Над ними — Orchestrator.** Не agent у строгому сенсі, а state machine
з LLM-powered маршрутизацією (W3 поглибить).

### Supervisor vs delegate pattern

Коли у вас > 2 agent'ів, виникає архітектурне питання: **хто кому
передає управління?** Є два канонічні патерни, і вибір визначає як
ви будете дебажити інцидент о 2:00 ночі.

**Supervisor (hub-and-spoke).** Один Orchestrator володіє state та
викликає specialist'ів по одному. Кожен specialist повертає відповідь
*Orchestrator'у*, а не наступному agent'у. Orchestrator вирішує, куди далі.

```
                        ┌─────────────────┐
                        │   Orchestrator  │  ← володіє state
                        │   (supervisor)  │  ← вирішує next step
                        └──┬────┬────┬────┬──┘
                     call│ ▲  │ ▲  │ ▲  │ ▲
                         ▼ │  ▼ │  ▼ │  ▼ │ result
                   ┌─────┴┐ ┌┴────┐ ┌┴───────┐ ┌┴──────────┐
                   │Intake│ │ KYC │ │ Credit  │ │Underwriter│
                   └──────┘ └─────┘ │ History │ └───────────┘
                                   └─────────┘
       specialist'и не бачать одне одного — тільки Orchestrator
```

**Delegate (peer-to-peer handoff).** Agent A сам вирішує, кому
передати управління, та викликає Agent B напряму. B далі викликає C.
State «тече» разом з викликом. Orchestrator'а як окремого компонента
немає — його роль розмита по всіх agent'ах.

```
     ┌───────┐  handoff   ┌───────┐  handoff   ┌───────┐  handoff   ┌──────────┐
     │Intake │ ─────────▶ │  KYC  │ ─────────▶ │Credit │ ─────────▶ │Underwrtr │
     └───────┘            └───────┘            │History│            └──────────┘
         ▲                    │                └───────┘                 │
         │                    │ failure?            │                    │
         └────────────────────┴─────────────────────┴────────────────────┘
                              хто ловить? хто логує? хто retry?
```

**Trade-offs:**

| Критерій | Supervisor | Delegate |
|----------|------------|----------|
| Audit log | Централізований, 1 місце | Розкиданий по agent'ах |
| State ownership | Один власник (Orchestrator) | Shared / passed around |
| Debug інциденту | `grep orchestrator.log` | Reconstruct trace з N logs |
| Нова роль | Add node + 1 route | Edit N agent'ів (хто про неї знає) |
| LLM calls | +1 per step (routing) | Дешевше (agent сам знає next) |
| Loops / циклічні handoff | Неможливі by design | Легко випадково зробити A→B→A |
| Human-in-the-loop | Natural pause point | Треба вбудовувати в кожен agent |

**Для Loan Copilot беремо supervisor pattern.** Три причини:

1. **Regulatory audit.** НБУ питає «чому ви відмовили клієнту X о 14:32?» —
   відповідь має бути в одному місці, не у 7 agent-логах.
2. **Детермінізм маршрутизації.** `KYC passed → CreditHistory` —
   це бізнес-правило, а не LLM-рішення. Orchestrator — єдиний, хто
   інтерпретує політику.
3. **Human-in-the-loop.** На будь-якому кроці Orchestrator може
   поставити заявку на `manual_review` — specialist'и про це не знають
   і не повинні.

Delegate pattern корисний для research-style агентів (deep research,
coding assistants), де гнучкість маршрутизації важливіша за
audit. У кредитному конвеєрі — ні.

### Контракти I/O

Правило #1: **кожен agent повертає Pydantic model, не plain dict**. Це
дає статичну перевірку, автогенерацію JSON schema для tools, та легкі
unit tests.

Правило #2: **кожна передача управління — через `AgentResponse` з
`next_action` + `handoff_to`**. Нема прихованого shared state.

Правило #3: **agent бачить тільки свій scope**. Intake не знає про
credit history score. Underwriter не знає про e-sign. Orchestrator — єдиний,
хто бачить full picture.

## Code

Дивіться [`common/schemas.py`](../common/schemas.py). Ключові моделі:

```python
class Application(BaseModel):
    application_id: str
    applicant: Applicant
    request: LoanRequest
    kyc_passed: bool | None = None
    credit_score: int | None = Field(None, ge=300, le=850)
    decision: Literal["approve", "decline", "manual_review"] | None = None
    rate_apr: Decimal | None = None


class AgentResponse(BaseModel):
    agent: str                                    # хто відповів
    content: str                                  # текст для клієнта/логу
    next_action: Literal["handoff", "ask_user", "done", "escalate"]
    handoff_to: str | None = None                 # до якого agent'а
    data: dict = Field(default_factory=dict)      # структуровані зміни
```

**Чому `kyc_passed` та `credit_score` — у єдиному `Application`?** Це
immutable-ish shared state, яким володіє Orchestrator. Agent'и
читають та пропонують зміни через `AgentResponse.data`, Orchestrator
застосовує — це audit log «who changed what when».

## Try it

У REPL (або `python -c`):

```python
from common.schemas import AgentResponse

msg = AgentResponse(
    agent="IntakeAgent",
    content="Усі поля зібрано. Передаю на KYC.",
    next_action="handoff",
    handoff_to="KYCAgent",
    data={"intake_complete": True},
)

print(msg.model_dump_json(indent=2))
```

Побачите валідований JSON. Якщо вкажете `next_action="handoff"` без
`handoff_to` — Pydantic не поскаржиться (це дозволено для `done`).
**Додайте власний validator**, що вимагає `handoff_to` для `handoff`.
Це задача на 3 хвилини — спробуйте.

<details>
<summary>Hint</summary>

```python
from pydantic import model_validator

@model_validator(mode="after")
def _handoff_target_required(self):
    if self.next_action == "handoff" and not self.handoff_to:
        raise ValueError("handoff requires handoff_to")
    return self
```

</details>

## Next

[03-first-agent.md](./03-first-agent.md) · побудуємо перший agent (Intake)
з повним кодом.
