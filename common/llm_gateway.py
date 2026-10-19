"""Тонкий LLM gateway · одна точка виклику для всіх модулів.

Чому не LangChain `ChatOpenAI` напряму? У production ви не хочете, щоб
7 agent'ів знали про конкретну модель. Gateway дає:

- auto-fallback між provider'ами (DIAL / OpenAI / Anthropic)
- єдиний інтерфейс
- точка для observability + cost tracking (додамо у W4)

Для workshop це ~70 рядків; у production — окремий service (W2, Tier D).

Primary provider = **DIAL** (EPAM AI proxy, OpenAI-compatible). Використовує
той самий API-key що й CodeMie. Fallback — direct OpenAI / Anthropic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

Provider = Literal["dial", "openai", "anthropic", "auto"]


@dataclass
class ChatResult:
    content: str
    provider: str
    model: str


# ──────────────────────────── DIAL (EPAM) ────────────────────────────
#
# DIAL — EPAM-internal LLM proxy. OpenAI-compatible API, але:
#   - auth через header `Api-Key: <key>` (не `Authorization: Bearer`)
#   - потребує query param `api-version=YYYY-MM-DD` (Azure-style)
#   - models: `gpt-4o-mini-2024-07-18`, `gpt-4.1-2025-04-14`,
#             `anthropic.claude-3-5-sonnet-20241022-v2:0`, etc.
#
# Base URL прикл.: https://ai-proxy.lab.epam.com/openai
# Той самий API-key, що налаштований у CodeMie Assistant settings.


def _call_dial(messages: list[dict], model: str, temperature: float) -> ChatResult:
    from openai import OpenAI

    base_url = os.environ["DIAL_API_URL"].rstrip("/")
    api_key = os.environ["DIAL_API_KEY"]
    api_version = os.getenv("DIAL_API_VERSION", "2024-10-21")

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_query={"api-version": api_version},
        default_headers={"Api-Key": api_key},
    )
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature
    )
    return ChatResult(resp.choices[0].message.content or "", "dial", model)


# ─────────────────────── direct OpenAI (fallback) ───────────────────────


def _call_openai(messages: list[dict], model: str, temperature: float) -> ChatResult:
    from openai import OpenAI

    client = OpenAI()
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature
    )
    return ChatResult(resp.choices[0].message.content or "", "openai", model)


# ────────────────────── direct Anthropic (fallback) ──────────────────────


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


# ──────────────────────────── public API ────────────────────────────


def _resolve_provider() -> Provider:
    """Auto-detect provider based on env vars. DIAL пріоритетний."""
    forced = (os.getenv("LLM_PROVIDER") or "auto").lower()
    if forced in {"dial", "openai", "anthropic"}:
        return forced  # type: ignore[return-value]

    if os.getenv("DIAL_API_KEY") and os.getenv("DIAL_API_URL"):
        return "dial"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError(
        "No LLM provider configured. Set DIAL_API_KEY+DIAL_API_URL (EPAM), "
        "or OPENAI_API_KEY, or ANTHROPIC_API_KEY."
    )


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
        provider = _resolve_provider()

    if provider == "dial":
        model = os.getenv("DIAL_MODEL", "gpt-4o-mini-2024-07-18")
        return _call_dial(messages, model, temperature)
    if provider == "openai":
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return _call_openai(messages, model, temperature)
    if provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
        return _call_anthropic(messages, model, temperature)
    raise ValueError(f"Unknown provider: {provider}")
