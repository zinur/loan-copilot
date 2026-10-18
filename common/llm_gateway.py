"""Тонкий LLM gateway · одна точка виклику для всіх модулів.

Чому не LangChain `ChatOpenAI` напряму? У production ви не хочете, щоб
7 agent'ів знали про конкретну модель. Gateway дає:

- auto-fallback між provider'ами
- єдиний інтерфейс
- точка для observability + cost tracking (додамо у W4)

Для workshop це 30 рядків; у production — окремий service (W2, Tier D).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

Provider = Literal["openai", "anthropic", "auto"]


@dataclass
class ChatResult:
    content: str
    provider: str
    model: str


def _call_openai(messages: list[dict], model: str, temperature: float) -> ChatResult:
    from openai import OpenAI

    client = OpenAI()
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature
    )
    return ChatResult(resp.choices[0].message.content or "", "openai", model)


def _call_anthropic(messages: list[dict], model: str, temperature: float) -> ChatResult:
    from anthropic import Anthropic

    client = Anthropic()
    # Anthropic очікує system поза messages
    system = ""
    conv: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            conv.append(m)
    resp = client.messages.create(
        model=model,
        system=system or "You are a helpful assistant.",
        messages=conv,
        max_tokens=1024,
        temperature=temperature,
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    return ChatResult(text, "anthropic", model)


def chat(
    messages: list[dict],
    *,
    provider: Provider = "auto",
    temperature: float = 0.2,
) -> ChatResult:
    """Виклик LLM з уніфікованим API.

    `messages` — OpenAI-стилю: `[{"role": "system|user|assistant", "content": "..."}]`.
    """
    if provider == "auto":
        provider = (os.getenv("LLM_PROVIDER") or "auto").lower()  # type: ignore
    if provider == "auto":
        provider = "openai" if os.getenv("OPENAI_API_KEY") else "anthropic"

    if provider == "openai":
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return _call_openai(messages, model, temperature)
    if provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
        return _call_anthropic(messages, model, temperature)
    raise ValueError(f"Unknown provider: {provider}")
