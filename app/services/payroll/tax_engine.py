"""Income-tax (TDS) estimator — Phase 2.

Pure Decimal functions, no DB access. Projects an employee's annual tax from a
monthly salary basis and the IT declaration, then spreads it across the year to
a monthly TDS figure. Both regimes (Old / New) are supported.

IMPORTANT — this is a versioned ESTIMATE, not filing-grade tax:
- Rules below are FY 2025-26 to the best of current knowledge and MUST be
  verified against the Finance Act before any real reliance.
- v1 simplifications: HRA exemption is NOT computed (needs basic + city + actual
  rent); surcharge on very high incomes is NOT applied; marginal relief is
  ignored. These are documented so the snapshot is auditable.
- The version string is snapshotted onto each payslip (like the statutory
  ruleset) so historical payslips never change when the rules are updated.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.payroll.constants import TaxRegime

# Bump when any number below changes; snapshotted onto every payslip's TDS block.
TDS_RULESET_VERSION = "IN-TDS-v1-FY2025-26"

_CESS_RATE = Decimal("0.04")  # Health & Education cess on tax after rebate
_MONTHS = Decimal("12")

# Slabs: ascending (upper_inclusive | None, rate). None upper = "and above".
_NEW_REGIME_SLABS: list[tuple[Decimal | None, Decimal]] = [
    (Decimal("400000"), Decimal("0")),
    (Decimal("800000"), Decimal("0.05")),
    (Decimal("1200000"), Decimal("0.10")),
    (Decimal("1600000"), Decimal("0.15")),
    (Decimal("2000000"), Decimal("0.20")),
    (Decimal("2400000"), Decimal("0.25")),
    (None, Decimal("0.30")),
]
_OLD_REGIME_SLABS: list[tuple[Decimal | None, Decimal]] = [
    (Decimal("250000"), Decimal("0")),
    (Decimal("500000"), Decimal("0.05")),
    (Decimal("1000000"), Decimal("0.20")),
    (None, Decimal("0.30")),
]

_NEW_STD_DEDUCTION = Decimal("75000")
_OLD_STD_DEDUCTION = Decimal("50000")

# 87A rebate: taxable income at/under the threshold => tax reduced to zero.
_NEW_REBATE_LIMIT = Decimal("1200000")  # Budget 2025: nil tax up to ₹12L (new)
_OLD_REBATE_LIMIT = Decimal("500000")

# Declared-deduction caps (Old regime only).
_CAP_80C = Decimal("150000")
_CAP_80D = Decimal("100000")
_CAP_HOME_LOAN = Decimal("200000")


def _q(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _slab_tax(taxable: Decimal, slabs: list[tuple[Decimal | None, Decimal]]) -> Decimal:
    """Progressive tax across ascending slabs."""
    tax = Decimal("0")
    lower = Decimal("0")
    for upper, rate in slabs:
        if upper is None:
            tax += (taxable - lower) * rate if taxable > lower else Decimal("0")
            break
        if taxable > upper:
            tax += (upper - lower) * rate
            lower = upper
        else:
            if taxable > lower:
                tax += (taxable - lower) * rate
            break
    return tax


def _old_regime_deductions(declarations: dict[str, Any]) -> Decimal:
    """Sum of declared deductions, each capped (Old regime). HRA is excluded in
    v1 (its exemption needs basic salary + city + actual rent)."""

    def amt(key: str) -> Decimal:
        return Decimal(str(declarations.get(key) or "0"))

    return (
        min(amt("declared_80c"), _CAP_80C)
        + min(amt("declared_80d"), _CAP_80D)
        + min(amt("declared_home_loan_interest"), _CAP_HOME_LOAN)
        + amt("declared_other")
    )


def compute_tds(
    *,
    annual_gross: Decimal,
    regime: str,
    declarations: dict[str, Any] | None = None,
    prev_employer_income: Decimal = Decimal("0"),
    prev_employer_tds: Decimal = Decimal("0"),
    new_rebate_limit: Decimal = _NEW_REBATE_LIMIT,
    old_rebate_limit: Decimal = _OLD_REBATE_LIMIT,
    new_std_deduction: Decimal = _NEW_STD_DEDUCTION,
    old_std_deduction: Decimal = _OLD_STD_DEDUCTION,
) -> dict[str, Any]:
    """Estimate annual tax and the monthly TDS to withhold.

    annual_gross: projected gross for the year from THIS employer (e.g. monthly
    gross x 12). prev_employer_* fold in prior income/TDS already taxed/deducted.
    Rebate limits and standard deductions default to the code constants but may
    be overridden per company (Settings → Statutory Compliance); the slab
    brackets and 80C/80D caps stay code-defined.
    """
    declarations = declarations or {}
    is_old = regime == TaxRegime.OLD.value

    total_income = annual_gross + prev_employer_income
    std_deduction = old_std_deduction if is_old else new_std_deduction
    declared = _old_regime_deductions(declarations) if is_old else Decimal("0")

    taxable_income = total_income - std_deduction - declared
    if taxable_income < 0:
        taxable_income = Decimal("0")

    slabs = _OLD_REGIME_SLABS if is_old else _NEW_REGIME_SLABS
    rebate_limit = old_rebate_limit if is_old else new_rebate_limit

    tax_before_rebate = _slab_tax(taxable_income, slabs)
    rebate = tax_before_rebate if taxable_income <= rebate_limit else Decimal("0")
    tax_after_rebate = tax_before_rebate - rebate
    cess = tax_after_rebate * _CESS_RATE
    annual_tax = tax_after_rebate + cess

    # TDS still to deduct from this employer, spread over the year.
    tds_remaining = annual_tax - prev_employer_tds
    if tds_remaining < 0:
        tds_remaining = Decimal("0")
    monthly_tds = _q(tds_remaining / _MONTHS)

    return {
        "version": TDS_RULESET_VERSION,
        "regime": regime,
        "annual_gross": _q(annual_gross),
        "total_income": _q(total_income),
        "standard_deduction": _q(std_deduction),
        "declared_deductions": _q(declared),
        "taxable_income": _q(taxable_income),
        "tax_before_rebate": _q(tax_before_rebate),
        "rebate_87a": _q(rebate),
        "cess": _q(cess),
        "annual_tax": _q(annual_tax),
        "prev_employer_tds": _q(prev_employer_tds),
        "monthly_tds": monthly_tds,
    }
