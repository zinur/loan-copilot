"""Mock data для прикладів. Жоден реальний API не викликається у workshop."""

from decimal import Decimal

from ..schemas import Applicant, Application, LoanPurpose, LoanRequest

SAMPLE_APPLICATION = Application(
    application_id="LC-2026-00042",
    applicant=Applicant(
        full_name="Марія Коваленко",
        tax_id="1234567890",
        phone="+380501234567",
        email="maria@example.com",
        monthly_income=Decimal("35000"),
    ),
    request=LoanRequest(
        amount=Decimal("150000"),
        term_months=24,
        purpose=LoanPurpose.consumer,
    ),
)

MOCK_BUREAU_SCORES = {
    "1234567890": 720,  # Марія — good
    "9876543210": 580,  # поганий кейс
    "5555555555": 650,  # borderline
}

MOCK_KYC_RESULTS = {
    "1234567890": {"passed": True, "sanctions": False, "pep": False},
    "9876543210": {"passed": True, "sanctions": False, "pep": False},
    "5555555555": {"passed": False, "sanctions": True, "pep": False},
}
