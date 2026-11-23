"""
calculators.py
==============
Deterministic banking calculators. The LLM never does the arithmetic: it calls these functions
(function calling) and the answer shows their breakdown.

* calculate_loan_provision — classify loans by how long they are overdue and compute the minimum
  loan-loss provision (कर्जा नोक्सानी व्यवस्था) per the unified directive.
* check_cash_transaction   — is a cash payment at or above the legal cash-transaction limit?

Figures come exclusively from verified rows in bank_parameters.csv; when they are missing for the
requested fiscal year the calculators raise `ParametersUnavailable`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.fiscal_year import normalize_fy_label
from tools.parameters import LOAN_CATEGORIES, ParametersUnavailable, ParameterTable

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal(100)
CATEGORY_NAMES = {
    "pass": ("Pass", "असल"), "watchlist": ("Watch list", "सूक्ष्म निगरानी"), "substandard": ("Substandard", "कमसल"),
    "doubtful": ("Doubtful", "शंकास्पद"), "loss": ("Loss", "खराब"),
}
Family = Literal["abc", "mfi", "nifra"]


def _money(x: Decimal) -> Decimal:
    return x.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class Loan(BaseModel):
    """One loan: outstanding principal and either months overdue or a known category."""

    amount: Decimal = Field(ge=0, le=Decimal("1e13"), description="Outstanding principal in NPR")
    months_overdue: Decimal | None = Field(default=None, ge=0, le=1200, description="Months the payment is overdue")
    category: Literal["pass", "watchlist", "substandard", "doubtful", "loss"] | None = None


class ProvisionInput(BaseModel):
    """Input for the loan-loss provision calculator."""

    fiscal_year: str = Field(description='Fiscal year, e.g. "2083/84"')
    family: Family = "abc"
    loans: list[Loan] = Field(min_length=1, max_length=200)


class CashInput(BaseModel):
    """Input for the cash-transaction limit check."""

    fiscal_year: str
    amount: Decimal = Field(ge=0, le=Decimal("1e13"), description="Cash amount of ONE transaction in NPR")


def _category_for(months: Decimal, bands: dict) -> str:
    """Loan category from months overdue: the band whose (min, max] contains it (first band includes 0)."""
    for cat in LOAN_CATEGORIES:
        b = bands[cat]
        lo = b.min_months if b.min_months is not None else Decimal(0)
        hi = b.max_months
        above_lo = months > lo or (cat == LOAN_CATEGORIES[0] and months >= lo)
        if above_lo and (hi is None or months <= hi):
            return cat
    return "loss"


def calculate_loan_provision(data: ProvisionInput, table: ParameterTable) -> dict:
    """Minimum loan-loss provision for a list of loans using the verified rates for the FY."""
    fy = normalize_fy_label(data.fiscal_year)
    if fy is None:
        raise ParametersUnavailable(f"Invalid fiscal year {data.fiscal_year!r}.")
    rates = table.loan_provisioning(fy, data.family)
    bands = table.overdue_bands(fy, data.family) if any(l.category is None for l in data.loans) else {}

    rows, total, total_amount = [], Decimal(0), Decimal(0)
    for i, loan in enumerate(data.loans, 1):
        if loan.category is None and loan.months_overdue is None:
            raise ValueError(f"loan {i}: give months_overdue or category")
        cat = loan.category or _category_for(loan.months_overdue or Decimal(0), bands)
        rate = rates[cat].value
        provision = _money(loan.amount * rate / HUNDRED)
        total += provision
        total_amount += loan.amount
        rows.append({
            "loan": i, "amount": float(_money(loan.amount)),
            "months_overdue": float(loan.months_overdue) if loan.months_overdue is not None else None,
            "category": cat, "category_en": CATEGORY_NAMES[cat][0], "category_ne": CATEGORY_NAMES[cat][1],
            "rate_percent": float(rate), "provision": float(provision),
        })
    sources = sorted({p.source for p in rates.values()} | {b.source for b in bands.values()})
    amendments = sorted({p.amended_by for p in rates.values() if p.amended_by})
    notes = ["Minimum provision on outstanding principal, from the human-verified rates for this fiscal year.",
             "Special cases (rescheduled/restructured loans, loans that must be classified as loss whatever "
             "the overdue period, sector relief circulars) are not modelled."]
    if amendments:
        notes.append("Circulars amend this provision; check them: " + "; ".join(amendments)[:400])
    return {
        "type": "loan_provision", "fiscal_year": fy, "family": data.family,
        "rows": rows, "total_amount": float(_money(total_amount)), "total_provision": float(_money(total)),
        "rate_source": "; ".join(sources), "notes": notes,
    }


def check_cash_transaction(data: CashInput, table: ParameterTable) -> dict:
    """Whether one cash transaction reaches the limit above which a bank/banking instrument must be used."""
    fy = normalize_fy_label(data.fiscal_year)
    if fy is None:
        raise ParametersUnavailable(f"Invalid fiscal year {data.fiscal_year!r}.")
    limit = table.cash_limit(fy)
    over = data.amount >= limit.value
    return {
        "type": "cash_limit", "fiscal_year": fy, "amount": float(_money(data.amount)),
        "limit": float(_money(limit.value)), "at_or_above_limit": over,
        "rate_source": limit.source,
        "notes": [
            "At or above the limit, pay through a bank or financial institution or a banking instrument "
            "(cheque, transfer, card, wallet) unless one of the exceptions in the notice applies."
            if over else "Below the limit: cash is allowed for this single transaction.",
            "Exceptions include deposits, savings and currency exchange at a financial institution and "
            "repaying a bank loan; read the cited notice for the full list.",
        ],
    }


NO_CALCULATION = "answer_without_calculation"

# JSON schemas offered to the LLM for function calling (fiscal_year is filled in by the engine).
# The engine makes the model pick one of them, so a computation can never be done by the model itself;
# NO_CALCULATION is the choice for questions that only ask for a rule.
TOOL_SPECS: list[dict] = [
    {
        "name": "calculate_loan_provision",
        "description": "Classify bank loans by months overdue (pass / watch list / substandard / doubtful / loss) "
                       "and compute the minimum loan-loss provision required by the NRB unified directive, using "
                       "verified rates. Use when the user asks how much provision is needed or which category a "
                       "loan falls in.",
        "parameters": {
            "type": "object",
            "properties": {
                "family": {"type": "string", "enum": ["abc", "mfi", "nifra"],
                           "description": "abc = commercial/development banks and finance companies (default), "
                                          "mfi = microfinance, nifra = infrastructure development bank"},
                "loans": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number", "description": "Outstanding principal in NPR"},
                            "months_overdue": {"type": "number", "description": "Months overdue (0 if current)"},
                            "category": {"type": "string",
                                         "enum": ["pass", "watchlist", "substandard", "doubtful", "loss"]},
                        },
                        "required": ["amount"],
                    },
                },
            },
            "required": ["loans"],
        },
    },
    {
        "name": "check_cash_transaction",
        "description": "Check whether a single cash payment/transaction is at or above Nepal's legal cash-"
                       "transaction limit (above which it must go through a bank or banking instrument).",
        "parameters": {
            "type": "object",
            "properties": {"amount": {"type": "number", "description": "Cash amount of one transaction in NPR"}},
            "required": ["amount"],
        },
    },
    {
        "name": NO_CALCULATION,
        "description": "Choose this when the user does not ask to compute anything with their own amounts "
                       "(they only ask what a rule, rate or limit is).",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "One short reason"}},
        },
    },
]


def run_tool(name: str, args: dict, fiscal_year: str, table: ParameterTable) -> dict:
    """Execute a calculator requested by the LLM. Errors are returned as data, not raised."""
    kind = {"calculate_loan_provision": "loan_provision", "check_cash_transaction": "cash_limit"}.get(name, name)
    try:
        if name == "calculate_loan_provision":
            return calculate_loan_provision(ProvisionInput(fiscal_year=fiscal_year, **args), table)
        if name == "check_cash_transaction":
            return check_cash_transaction(CashInput(fiscal_year=fiscal_year, **args), table)
        return {"type": name, "fiscal_year": fiscal_year, "error": f"Unknown calculator {name}"}
    except ParametersUnavailable as exc:
        return {"type": kind, "fiscal_year": fiscal_year, "error": str(exc)}
    except (ValueError, TypeError) as exc:
        return {"type": kind, "fiscal_year": fiscal_year, "error": f"Invalid input: {exc}"}
