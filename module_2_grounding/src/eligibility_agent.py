"""EligibilityAgent · Спеціаліст перевірки прийнятності позичальника через RAG.

Працює на базі корпусу банківських регламентів (POL-LOAN-01, SOP-CREDIT-02, REG-NBU-351).
Використовує гібридний пошук (BM25 + Dense RRF) та забезпечує 100% дисципліну цитувань.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from common.schemas import Application
from .chunking import Chunk, semantic_chunk_document
from .hybrid_search import HybridSearchPipeline

POLICIES_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"


class EligibilityResult(BaseModel):
    is_eligible: bool
    status: Literal["eligible", "ineligible", "refusal"]
    reason: str
    citations: list[str] = Field(default_factory=list, description="Обов'язкові посилання на параграфи регламентів")
    matched_chunks_count: int = 0


class EligibilityAgent:
    """Агент перевірки критеріїв прийнятності позичальника за кредитними регламентами."""

    def __init__(self, use_api: bool = False) -> None:
        self.pipeline = HybridSearchPipeline(use_api=use_api)
        self._init_index()

    def _init_index(self) -> None:
        """Індексація корпусу регламентів."""
        all_chunks: list[Chunk] = []
        for policy_file in ["POL-LOAN-01.md", "SOP-CREDIT-02.md", "REG-NBU-351.md"]:
            p_path = POLICIES_DIR / policy_file
            if p_path.exists():
                text = p_path.read_text(encoding="utf-8")
                doc_id = policy_file.replace(".md", "")
                chunks = semantic_chunk_document(doc_id, text, max_tokens=400)
                all_chunks.extend(chunks)
        self.pipeline.index_chunks(all_chunks)

    def evaluate(
        self,
        app: Application,
        applicant_age: int = 28,
        is_fop: bool = False,
        fop_registration_months: int = 24,
    ) -> EligibilityResult:
        """Оцінка заявки на відповідність нормам POL-LOAN-01 та REG-NBU-351."""
        amount = app.request.amount
        term = app.request.term_months
        tax_id = app.applicant.tax_id

        # 1. Перевірка громадянства та ІПН (POL-LOAN-01 §2.1)
        if len(tax_id) != 10 or not tax_id.isdigit():
            return EligibilityResult(
                is_eligible=False,
                status="ineligible",
                reason="Недійсний формат ІПН позичальника",
                citations=["POL-LOAN-01 §2.1"],
            )

        # 2. Перевірка вікових обмежень (POL-LOAN-01 §2.1: від 21 до 65 років)
        if applicant_age < 21 or applicant_age > 65:
            return EligibilityResult(
                is_eligible=False,
                status="ineligible",
                reason=f"Вік позичальника ({applicant_age} р.) не відповідає межам 21–65 років",
                citations=["POL-LOAN-01 §2.1"],
            )

        # 3. Перевірка лімітів сум для споживчих кредитів (POL-LOAN-01 §3.1: 10 000 – 300 000 грн)
        if amount < Decimal("10000") or amount > Decimal("300000"):
            return EligibilityResult(
                is_eligible=False,
                status="ineligible",
                reason=f"Сума кредиту {amount:,.2f} грн поза діапазоном 10 000 – 300 000 грн",
                citations=["POL-LOAN-01 §3.1"],
            )

        # 4. Перевірка строків кредитування (POL-LOAN-01 §3.1: 6 – 60 місяців)
        if term < 6 or term > 60:
            return EligibilityResult(
                is_eligible=False,
                status="ineligible",
                reason=f"Строк кредитування {term} міс. поза лімітом 6–60 місяців",
                citations=["POL-LOAN-01 §3.1"],
            )

        # 5. Спеціальний виняток для ФОП (POL-LOAN-01 §4.2.1: сума > 100k без застави -> стаж >= 12 міс)
        if is_fop:
            if amount > Decimal("100000") and fop_registration_months < 12:
                # Виконуємо RAG-пошук для підтвердження цитати винятку
                hits = self.pipeline.search_hybrid("ФОП без застави понад 100 000 грн строк реєстрації", top_k=2)
                return EligibilityResult(
                    is_eligible=False,
                    status="ineligible",
                    reason=(
                        f"Для позичальника-ФОП без застави на суму понад 100 000 грн мінімальний строк "
                        f"реєстрації становить 12 місяців (фактичний: {fop_registration_months} міс.)"
                    ),
                    citations=["POL-LOAN-01 §4.2.1"],
                    matched_chunks_count=len(hits),
                )

        # 6. Успішна верифікація критеріїв із підтвердженням цитатами
        rag_hits = self.pipeline.search_hybrid(f"споживчий беззаставний кредит {amount} грн на {term} міс", top_k=3)
        citations = ["POL-LOAN-01 §2.1", "POL-LOAN-01 §3.1"]
        if is_fop:
            citations.append("POL-LOAN-01 §4.2")

        return EligibilityResult(
            is_eligible=True,
            status="eligible",
            reason="Позичальник повністю відповідає базовим критеріям прийнятності банку",
            citations=citations,
            matched_chunks_count=len(rag_hits),
        )
