"""FinOps-модель юніт-економіки кредитного конвеєра при масштабуванні."""

from __future__ import annotations

from pydantic import BaseModel


class TierPricing(BaseModel):
    """Тарифи провайдерів за 1 мільйон токенів (USD)."""
    prompt_per_1m: float
    completion_per_1m: float
    cached_prompt_per_1m: float


# Стандартні корпоративні тарифи (наприклад, Azure OpenAI / DIAL)
FAST_LLM_TIER = TierPricing(
    prompt_per_1m=0.15,
    completion_per_1m=0.60,
    cached_prompt_per_1m=0.0375,  # 75% знижка на кешовані токени
)

REASONING_LLM_TIER = TierPricing(
    prompt_per_1m=2.50,
    completion_per_1m=10.00,
    cached_prompt_per_1m=0.625,
)

# Вартість виклику зовнішніх та внутрішніх інструментів
TOOL_COSTS = {
    "kyc_lookup": 0.80,          # Санкційні списки та реєстри PEP
    "credit_history_pull": 8.50, # Запит до Бюро кредитних історій
    "evaluate_risk_tier": 0.05,  # Внутрішня скорингова модель банку
}


def calculate_case_cost(
    is_completed: bool = True,
    use_prompt_caching: bool = True,
) -> dict[str, float]:
    """Розрахунок повної собівартості обробки однієї кредитної заявки.
    
    Враховує розбивку на рівні моделей (Tier 1 vs Tier 2) та інструменти.
    """
    # 1. Intake & Routing (Tier 1 Fast LLM): ~2000 prompt, 500 completion
    p_cache_rate = 0.80 if use_prompt_caching else 0.0
    intake_prompt_cost = (
        (2000 * (1 - p_cache_rate) * FAST_LLM_TIER.prompt_per_1m / 1_000_000)
        + (2000 * p_cache_rate * FAST_LLM_TIER.cached_prompt_per_1m / 1_000_000)
    )
    intake_comp_cost = 500 * FAST_LLM_TIER.completion_per_1m / 1_000_000
    intake_total = intake_prompt_cost + intake_comp_cost

    # 2. Eligibility RAG (Tier 1 Fast LLM з Prompt Caching на регламентах): ~4000 prompt, 600 completion
    rag_prompt_cost = (
        (4000 * (1 - p_cache_rate) * FAST_LLM_TIER.prompt_per_1m / 1_000_000)
        + (4000 * p_cache_rate * FAST_LLM_TIER.cached_prompt_per_1m / 1_000_000)
    )
    rag_comp_cost = 600 * FAST_LLM_TIER.completion_per_1m / 1_000_000
    rag_total = rag_prompt_cost + rag_comp_cost

    # 3. Underwriting & Decision (Tier 2 Reasoning LLM): ~1500 prompt, 800 completion
    underwriter_prompt_cost = 1500 * REASONING_LLM_TIER.prompt_per_1m / 1_000_000
    underwriter_comp_cost = 800 * REASONING_LLM_TIER.completion_per_1m / 1_000_000
    underwriter_total = underwriter_prompt_cost + underwriter_comp_cost

    # 4. Зовнішні інструменти (з урахуванням воронки конверсії до підписання)
    # З 10 000 користувачів до виклику БКІ доходять 30% (після проходження Consent Gate)
    bureau_conversion = 0.30
    tools_total = (
        (TOOL_COSTS["credit_history_pull"] * bureau_conversion)
    )

    llm_total = intake_total + rag_total + underwriter_total
    infra_and_logs = 0.0035  # K8s, PostgreSQL чекпоінти, Langfuse трейси

    total_cost = llm_total + tools_total + infra_and_logs

    return {
        "llm_cost": round(llm_total, 4),
        "tools_cost": round(tools_total, 4),
        "infra_cost": round(infra_and_logs, 4),
        "total_case_cost": round(total_cost, 4),
    }


def project_funnel_economics(daily_starts: int = 10_000) -> dict[str, any]:
    """Проєкція щоденних і щомісячних витрат при масштабуванні."""
    single_case = calculate_case_cost(use_prompt_caching=True)
    cost_per_case = single_case["total_case_cost"]

    daily_spend = daily_starts * cost_per_case
    monthly_spend = daily_spend * 30

    # Традиційний банківський процес: $15 за заявку (оператор + ручний андеррайтинг)
    traditional_case_cost = 15.0
    traditional_monthly = daily_starts * traditional_case_cost * 30
    monthly_savings = traditional_monthly - monthly_spend

    return {
        "daily_starts": daily_starts,
        "cost_per_case_usd": cost_per_case,
        "daily_spend_usd": round(daily_spend, 2),
        "monthly_spend_usd": round(monthly_spend, 2),
        "traditional_monthly_usd": round(traditional_monthly, 2),
        "monthly_savings_usd": round(monthly_savings, 2),
        "savings_percentage": round((monthly_savings / traditional_monthly) * 100, 1),
    }
