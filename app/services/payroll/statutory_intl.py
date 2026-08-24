"""
International statutory payroll — Korea (KR), Japan (JP) and United States (US).

Computes the monthly EMPLOYEE deductions and EMPLOYER contributions for each country's
mandatory social insurance + a withholding income-tax ESTIMATE, in the same shape the
India engine (statutory.py / tax_engine.py) produces, so `compute_payslip` can dispatch
on the company's country.

⚠️ RATES ARE CONFIGURABLE DEFAULTS (2024/2025 published rates) and MUST be verified with a
local payroll/tax advisor — social-insurance rates, wage caps and tax brackets change yearly
and can vary by region (Japan prefecture, US state). Income tax here is a simplified
progressive estimate on annualized pay, not an exact withholding-table figure.

All money is Decimal. compute_*(monthly_gross, ...) → {"employee": [...], "employer": [...],
"income_tax": Decimal, "snapshot": {...}}.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

RULESET_VERSION = "intl-2024.1"

SUPPORTED_COUNTRIES = {"KR", "JP", "US"}

# Normalise the various ways a country can be stored to an ISO-ish code.
_ALIASES = {
    "korea": "KR",
    "south korea": "KR",
    "republic of korea": "KR",
    "kr": "KR",
    "kor": "KR",
    "japan": "JP",
    "jp": "JP",
    "jpn": "JP",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "u.s.": "US",
    "united states of america": "US",
    "india": "IN",
    "in": "IN",
    "ind": "IN",
}


def country_code(country: str | None) -> str:
    return _ALIASES.get((country or "").strip().lower(), (country or "").strip().upper())


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _progressive_tax(annual_taxable: Decimal, brackets: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Brackets = [(upper_bound_or_None_as_inf, rate), ...] ascending. Marginal calculation."""
    tax = Decimal("0")
    lower = Decimal("0")
    for upper, rate in brackets:
        if annual_taxable <= lower:
            break
        band_top = annual_taxable if (upper is None or annual_taxable < upper) else upper
        tax += (band_top - lower) * rate
        lower = upper if upper is not None else annual_taxable
    return tax if tax > 0 else Decimal("0")


# ---------------------------------------------------------------------------
# 🇰🇷 KOREA — 4대보험 (national pension, health, long-term care, employment) + 소득세
# ---------------------------------------------------------------------------
KR = {
    "pension_rate": Decimal("0.045"),  # 국민연금 employee 4.5% (employer 4.5%)
    "pension_income_cap": Decimal("6170000"),  # monthly income base cap (2024)
    "health_rate": Decimal("0.03545"),  # 건강보험 employee 3.545% (2024)
    "ltc_rate_of_health": Decimal("0.1295"),  # 장기요양 = 12.95% of health premium (2024)
    "employment_rate": Decimal("0.009"),  # 고용보험 employee 0.9%
    "accident_rate_employer": Decimal("0.007"),  # 산재보험 employer-only avg (varies by industry)
    # 소득세 annual brackets (taxable, KRW) — marginal. 지방소득세 = +10% of income tax.
    "tax_brackets": [
        (Decimal("14000000"), Decimal("0.06")),
        (Decimal("50000000"), Decimal("0.15")),
        (Decimal("88000000"), Decimal("0.24")),
        (Decimal("150000000"), Decimal("0.35")),
        (Decimal("300000000"), Decimal("0.38")),
        (Decimal("500000000"), Decimal("0.40")),
        (Decimal("1000000000"), Decimal("0.42")),
        (None, Decimal("0.45")),
    ],
    "annual_personal_deduction": Decimal("1500000"),
    "local_tax_rate": Decimal("0.10"),  # of income tax
}


def compute_kr(monthly_gross: Decimal) -> dict[str, Any]:
    pension_base = min(monthly_gross, KR["pension_income_cap"])
    pension = _q(pension_base * KR["pension_rate"])
    health = _q(monthly_gross * KR["health_rate"])
    ltc = _q(health * KR["ltc_rate_of_health"])
    employment = _q(monthly_gross * KR["employment_rate"])

    annual_taxable = max(Decimal("0"), monthly_gross * 12 - KR["annual_personal_deduction"])
    annual_tax = _progressive_tax(annual_taxable, KR["tax_brackets"])
    income_tax = _q(annual_tax / 12)
    local_tax = _q(income_tax * KR["local_tax_rate"])

    employee = [
        {"code": "KR_PENSION", "label": "National Pension (국민연금)", "amount": float(pension)},
        {"code": "KR_HEALTH", "label": "Health Insurance (건강보험)", "amount": float(health)},
        {"code": "KR_LTC", "label": "Long-Term Care (장기요양)", "amount": float(ltc)},
        {"code": "KR_EMP", "label": "Employment Insurance (고용보험)", "amount": float(employment)},
        {"code": "KR_TAX", "label": "Income Tax (소득세)", "amount": float(income_tax)},
        {"code": "KR_LOCAL", "label": "Local Income Tax (지방소득세)", "amount": float(local_tax)},
    ]
    employer = [
        {"code": "KR_PENSION_ER", "label": "National Pension (employer)", "amount": float(pension)},
        {"code": "KR_HEALTH_ER", "label": "Health Insurance (employer)", "amount": float(health)},
        {"code": "KR_LTC_ER", "label": "Long-Term Care (employer)", "amount": float(ltc)},
        {"code": "KR_EMP_ER", "label": "Employment Insurance (employer)", "amount": float(employment)},
        {
            "code": "KR_ACCIDENT_ER",
            "label": "Industrial Accident (산재, employer)",
            "amount": float(_q(monthly_gross * KR["accident_rate_employer"])),
        },
    ]
    return {
        "employee": employee,
        "employer": employer,
        "income_tax": income_tax + local_tax,
        "snapshot": {
            "country": "KR",
            "pension": float(pension),
            "health": float(health),
            "ltc": float(ltc),
            "employment": float(employment),
            "income_tax": float(income_tax),
            "local_tax": float(local_tax),
        },
    }


# ---------------------------------------------------------------------------
# 🇯🇵 JAPAN — 社会保険 (health, long-term care, pension, employment) + 源泉所得税
# ---------------------------------------------------------------------------
JP = {
    "health_rate": Decimal("0.0499"),  # 健康保険 employee ~4.99% (Tokyo 2024, half of ~9.98%)
    "ltc_rate": Decimal("0.0080"),  # 介護保険 employee ~0.80% (age 40–64), half of 1.60%
    "pension_rate": Decimal("0.0915"),  # 厚生年金 employee 9.15% (half of 18.3%)
    "pension_monthly_cap": Decimal("650000"),  # standard monthly remuneration cap
    "employment_rate": Decimal("0.006"),  # 雇用保険 employee 0.6% (2024)
    "employment_rate_employer": Decimal("0.0095"),
    "accident_rate_employer": Decimal("0.003"),  # 労災 employer-only (varies by industry)
    # 所得税 annual brackets (taxable, JPY) — marginal.
    "tax_brackets": [
        (Decimal("1950000"), Decimal("0.05")),
        (Decimal("3300000"), Decimal("0.10")),
        (Decimal("6950000"), Decimal("0.20")),
        (Decimal("9000000"), Decimal("0.23")),
        (Decimal("18000000"), Decimal("0.33")),
        (Decimal("40000000"), Decimal("0.40")),
        (None, Decimal("0.45")),
    ],
    "basic_deduction": Decimal("480000"),  # basic deduction (approx)
}


def _jp_employment_income_deduction(annual: Decimal) -> Decimal:
    """給与所得控除 (2020+): tiered employment-income deduction."""
    if annual <= Decimal("1625000"):
        return Decimal("550000")
    if annual <= Decimal("1800000"):
        return annual * Decimal("0.40") - Decimal("100000")
    if annual <= Decimal("3600000"):
        return annual * Decimal("0.30") + Decimal("80000")
    if annual <= Decimal("6600000"):
        return annual * Decimal("0.20") + Decimal("440000")
    if annual <= Decimal("8500000"):
        return annual * Decimal("0.10") + Decimal("1100000")
    return Decimal("1950000")  # capped


def compute_jp(monthly_gross: Decimal, age: int | None = None) -> dict[str, Any]:
    include_ltc = age is None or age >= 40
    health = _q(monthly_gross * JP["health_rate"])
    ltc = _q(monthly_gross * JP["ltc_rate"]) if include_ltc else Decimal("0.00")
    pension_base = min(monthly_gross, JP["pension_monthly_cap"])
    pension = _q(pension_base * JP["pension_rate"])
    employment = _q(monthly_gross * JP["employment_rate"])

    annual = monthly_gross * 12
    taxable = max(Decimal("0"), annual - _jp_employment_income_deduction(annual) - JP["basic_deduction"])
    annual_tax = _progressive_tax(taxable, JP["tax_brackets"])
    income_tax = _q(annual_tax / 12)

    employee = [
        {"code": "JP_HEALTH", "label": "Health Insurance (健康保険)", "amount": float(health)},
        *(
            [{"code": "JP_LTC", "label": "Long-Term Care (介護保険)", "amount": float(ltc)}]
            if include_ltc
            else []
        ),
        {"code": "JP_PENSION", "label": "Pension (厚生年金)", "amount": float(pension)},
        {"code": "JP_EMP", "label": "Employment Insurance (雇用保険)", "amount": float(employment)},
        {"code": "JP_TAX", "label": "Income Tax (源泉所得税)", "amount": float(income_tax)},
    ]
    employer = [
        {"code": "JP_HEALTH_ER", "label": "Health Insurance (employer)", "amount": float(health)},
        *(
            [{"code": "JP_LTC_ER", "label": "Long-Term Care (employer)", "amount": float(ltc)}]
            if include_ltc
            else []
        ),
        {"code": "JP_PENSION_ER", "label": "Pension (employer)", "amount": float(pension)},
        {
            "code": "JP_EMP_ER",
            "label": "Employment Insurance (employer)",
            "amount": float(_q(monthly_gross * JP["employment_rate_employer"])),
        },
        {
            "code": "JP_ACCIDENT_ER",
            "label": "Workers' Comp (労災, employer)",
            "amount": float(_q(monthly_gross * JP["accident_rate_employer"])),
        },
    ]
    return {
        "employee": employee,
        "employer": employer,
        "income_tax": income_tax,
        "snapshot": {
            "country": "JP",
            "health": float(health),
            "ltc": float(ltc),
            "pension": float(pension),
            "employment": float(employment),
            "income_tax": float(income_tax),
        },
    }


# ---------------------------------------------------------------------------
# 🇺🇸 UNITED STATES — FICA (Social Security + Medicare) + federal (+ optional state) tax
# ---------------------------------------------------------------------------
US = {
    "ss_rate": Decimal("0.062"),  # Social Security 6.2% (employer matches)
    "ss_wage_base_annual": Decimal("168600"),  # 2024 SS wage base
    "medicare_rate": Decimal("0.0145"),  # Medicare 1.45% (employer matches)
    "medicare_addl_rate": Decimal("0.009"),  # +0.9% over $200k (employee only)
    "medicare_addl_threshold_annual": Decimal("200000"),
    "futa_rate_employer": Decimal("0.006"),  # FUTA 0.6% (after credit) on first $7,000
    "futa_wage_base": Decimal("7000"),
    # Federal income tax — 2024 SINGLE, marginal; standard deduction applied first.
    "std_deduction": Decimal("14600"),
    "tax_brackets": [
        (Decimal("11600"), Decimal("0.10")),
        (Decimal("47150"), Decimal("0.12")),
        (Decimal("100525"), Decimal("0.22")),
        (Decimal("191950"), Decimal("0.24")),
        (Decimal("243725"), Decimal("0.32")),
        (Decimal("609350"), Decimal("0.35")),
        (None, Decimal("0.37")),
    ],
}


def compute_us(monthly_gross: Decimal, state_tax_rate: Decimal | None = None) -> dict[str, Any]:
    annual = monthly_gross * 12
    ss_base_monthly = min(monthly_gross, US["ss_wage_base_annual"] / 12)
    social_security = _q(ss_base_monthly * US["ss_rate"])
    medicare = _q(monthly_gross * US["medicare_rate"])
    if annual > US["medicare_addl_threshold_annual"]:
        medicare = _q(medicare + monthly_gross * US["medicare_addl_rate"])

    taxable = max(Decimal("0"), annual - US["std_deduction"])
    annual_fed = _progressive_tax(taxable, US["tax_brackets"])
    federal_tax = _q(annual_fed / 12)
    state_rate = state_tax_rate if state_tax_rate is not None else Decimal("0")
    state_tax = _q(monthly_gross * state_rate)

    employee = [
        {"code": "US_SS", "label": "Social Security", "amount": float(social_security)},
        {"code": "US_MEDICARE", "label": "Medicare", "amount": float(medicare)},
        {"code": "US_FED_TAX", "label": "Federal Income Tax", "amount": float(federal_tax)},
        *(
            [{"code": "US_STATE_TAX", "label": "State Income Tax", "amount": float(state_tax)}]
            if state_tax > 0
            else []
        ),
    ]
    employer = [
        {"code": "US_SS_ER", "label": "Social Security (employer)", "amount": float(social_security)},
        {
            "code": "US_MEDICARE_ER",
            "label": "Medicare (employer)",
            "amount": float(_q(monthly_gross * US["medicare_rate"])),
        },
        {
            "code": "US_FUTA_ER",
            "label": "FUTA (employer)",
            "amount": float(_q(min(monthly_gross, US["futa_wage_base"] / 12) * US["futa_rate_employer"])),
        },
    ]
    return {
        "employee": employee,
        "employer": employer,
        "income_tax": federal_tax + state_tax,
        "snapshot": {
            "country": "US",
            "social_security": float(social_security),
            "medicare": float(medicare),
            "federal_tax": float(federal_tax),
            "state_tax": float(state_tax),
        },
    }


def compute(
    country: str, monthly_gross: Decimal, *, age: int | None = None, state_tax_rate: Decimal | None = None
) -> dict[str, Any] | None:
    """Dispatch to the country engine. Returns None for unsupported countries (e.g. India,
    which is handled by the existing statutory.py / tax_engine.py path)."""
    code = country_code(country)
    if code == "KR":
        return compute_kr(monthly_gross)
    if code == "JP":
        return compute_jp(monthly_gross, age=age)
    if code == "US":
        return compute_us(monthly_gross, state_tax_rate=state_tax_rate)
    return None
