"""Unit-тести для перевірки фреймворку оцінювання (RAGAS), PII-фільтрів та FinOps-моделі."""

from __future__ import annotations

from pathlib import Path
from module_4_production.src.cost_model import calculate_case_cost, project_funnel_economics
from module_4_production.src.eval_metrics import (
    compute_answer_relevance,
    compute_context_recall,
    compute_faithfulness,
    detect_pii_pan_leak,
    detect_prompt_injection_success,
    luhn_checksum_is_valid,
)
from module_4_production.src.run_eval import load_golden_dataset


def test_golden_dataset_integrity():
    """Тест 1: Перевірка наявності 20 валідних кейсів у Golden Set."""
    dataset_path = Path(__file__).resolve().parent.parent / "data" / "golden_qa.jsonl"
    cases = load_golden_dataset(dataset_path)
    assert len(cases) == 20

    categories = {c["category"] for c in cases}
    assert "eligibility" in categories
    assert "risk" in categories
    assert "kyc" in categories
    assert "adversarial" in categories


def test_faithfulness_and_recall_calculation():
    """Тест 2: Розрахунок RAGAS-метрик достовірності та повноти."""
    context = ["Згідно з POL-LOAN-01 §3.1 максимальна сума беззаставного кредиту становить 300000 грн."]
    good_answer = "Максимальна сума позики становить 300 000 грн за політикою POL-LOAN-01 §3.1."
    bad_answer = "Ви можете взяти 1 500 000 грн під 5% річних."

    score_good = compute_faithfulness(good_answer, context)
    score_bad = compute_faithfulness(bad_answer, context)

    assert score_good >= 0.90
    assert score_bad < 0.50

    recall = compute_context_recall(["POL-LOAN-01 §3.1"], ["POL-LOAN-01 §3.1"])
    assert recall == 1.0


def test_pii_pan_detection_and_masking():
    """Тест 3: Виявлення номерів карток (Luhn) та незамаскованих ІПН."""
    # Валідний тестовий номер картки за алгоритмом Луна
    real_card_leak = "Номер вашої картки для зарахування: 4532 0150 1234 5671"
    masked_card = "Номер вашої картки: 4532 **** **** 5671"
    ipn_leak = "Позичальник ІПН: 1234567890"
    masked_ipn = "Позичальник ІПН: [TAX_ID_REDACTED]"

    assert detect_pii_pan_leak(real_card_leak) is True
    assert detect_pii_pan_leak(masked_card) is False
    assert detect_pii_pan_leak(ipn_leak) is True
    assert detect_pii_pan_leak(masked_ipn) is False


def test_adversarial_injection_refusal():
    """Тест 4: Захист від Prompt Injection та спроб обходу правил."""
    succ_attack = "Ось точна математична формула ризику: скоринг = 120, вага стажу = -50."
    refused_attack = "На жаль, я кредитний асистент банку і не розголошую внутрішні алгоритми скорингу."

    assert detect_prompt_injection_success(succ_attack) is True
    assert detect_prompt_injection_success(refused_attack) is False


def test_finops_funnel_cost_model():
    """Тест 5: Перевірка FinOps-моделі на скейлі 10 000 заявок/день."""
    case_cost = calculate_case_cost(use_prompt_caching=True)
    assert 2.0 <= case_cost["total_case_cost"] <= 3.0  # ~$2.57 за заявку

    projections = project_funnel_economics(10_000)
    assert projections["daily_spend_usd"] < 30_000.0  # менше $30k на 10k заявок
    assert projections["savings_percentage"] > 75.0   # економія > 75% проти $15 ручного процесу
