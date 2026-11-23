"""Deterministic calculators: only verified parameters, refusals otherwise."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from tools.calculators import (
    CashInput,
    Loan,
    ProvisionInput,
    calculate_loan_provision,
    check_cash_transaction,
    run_tool,
)
from tools.parameters import CSV_COLUMNS, ParametersUnavailable, ParameterTable

RATES = {"pass": "1", "watchlist": "5", "substandard": "25", "doubtful": "50", "loss": "100"}
BANDS = {"pass": ("", "1"), "watchlist": ("1", "3"), "substandard": ("3", "6"), "doubtful": ("6", "12"),
         "loss": ("12", "")}


def _row(**kw) -> dict:
    base = {k: "" for k in CSV_COLUMNS}
    base.update({"family": "abc", "effective_from_fy": "2082/83", "source_label": "SAMPLE UD 2082", "page": "1",
                 "verified": "true"})
    base.update(kw)
    return base


@pytest.fixture()
def table(tmp_path: Path) -> ParameterTable:
    rows = [_row(param_id=f"r{c}", parameter="loan_loss_provision", category=c, value=v, unit="percent")
            for c, v in RATES.items()]
    rows += [_row(param_id=f"b{c}", parameter="loan_overdue_band", category=c, value="0", unit="months",
                  min_months=lo, max_months=hi) for c, (lo, hi) in BANDS.items()]
    rows.append(_row(param_id="cash", parameter="cash_transaction_limit", category="single_transaction",
                     value="500000", unit="NPR", family="general"))
    rows.append(_row(param_id="old", parameter="loan_loss_provision", category="pass", value="1.2",
                     effective_from_fy="2081/82", effective_to_fy="2082/83", verified="true"))
    path = tmp_path / "bank_parameters.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return ParameterTable(path)


def test_provision_by_months_overdue(table: ParameterTable) -> None:
    loans = [Loan(amount=Decimal(1_000_000), months_overdue=Decimal(0)),
             Loan(amount=Decimal(1_000_000), months_overdue=Decimal(1)),     # boundary: still pass
             Loan(amount=Decimal(1_000_000), months_overdue=Decimal("1.5")),  # watch list
             Loan(amount=Decimal(1_000_000), months_overdue=Decimal(7)),      # doubtful
             Loan(amount=Decimal(1_000_000), months_overdue=Decimal(13))]     # loss
    out = calculate_loan_provision(ProvisionInput(fiscal_year="2083/84", loans=loans), table)
    assert [r["category"] for r in out["rows"]] == ["pass", "pass", "watchlist", "doubtful", "loss"]
    assert out["total_provision"] == 10_000 + 10_000 + 50_000 + 500_000 + 1_000_000


def test_overlap_year_uses_newest_edition(table: ParameterTable) -> None:
    out = calculate_loan_provision(ProvisionInput(fiscal_year="2082/83", loans=[Loan(amount=Decimal(100), category="pass")]),
                                   table)
    assert out["rows"][0]["rate_percent"] == 1.0  # the 2082 edition, not the 1.2% of 2081


def test_refuses_without_verified_rows(table: ParameterTable, tmp_path: Path) -> None:
    with pytest.raises(ParametersUnavailable):
        calculate_loan_provision(ProvisionInput(fiscal_year="2070/71", loans=[Loan(amount=Decimal(1), category="pass")]),
                                 table)
    unverified = tmp_path / "u.csv"
    text = table.path.read_text(encoding="utf-8-sig").replace(",true", ",false")
    unverified.write_text(text, encoding="utf-8-sig")
    with pytest.raises(ParametersUnavailable):
        check_cash_transaction(CashInput(fiscal_year="2083/84", amount=Decimal(1)), ParameterTable(unverified))


def test_cash_limit(table: ParameterTable) -> None:
    over = check_cash_transaction(CashInput(fiscal_year="2083/84", amount=Decimal(500_000)), table)
    under = check_cash_transaction(CashInput(fiscal_year="2083/84", amount=Decimal(499_999)), table)
    assert over["at_or_above_limit"] is True and under["at_or_above_limit"] is False and over["limit"] == 500000


def test_run_tool_returns_errors_as_data(table: ParameterTable) -> None:
    bad = run_tool("calculate_loan_provision", {"loans": [{"amount": 5}]}, "2083/84", table)
    assert "error" in bad and "months_overdue" in bad["error"]
    missing = run_tool("calculate_loan_provision", {"loans": [{"amount": 5, "category": "pass"}]}, "2060/61", table)
    assert missing["type"] == "loan_provision" and "not been checked" in missing["error"]
    assert "Unknown" in run_tool("nope", {}, "2083/84", table)["error"]
