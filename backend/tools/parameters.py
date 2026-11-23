"""
parameters.py
=============
Read the human-verified regulatory parameter table (data/processed/bank_parameters.csv).

`python -m ingest.review_parameters` finds candidate figures in the indexed directives and notices
(provisioning rates, overdue-period limits, the cash-transaction limit) together with the exact quote
and page; a person checks each row against the PDF and marks it verified. The calculators use ONLY
verified rows. Nothing in the code base contains a regulatory figure: when verified rows are missing
for the fiscal year, the calculators refuse (`ParametersUnavailable`).
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.fiscal_year import parse_fy

CSV_COLUMNS = [
    "param_id", "parameter", "category", "value", "unit", "min_months", "max_months", "family",
    "effective_from_fy", "effective_to_fy", "source_label", "source_file", "page", "source_chunk_id",
    "source_quote", "text_source", "amended_by", "verified",
]
RANGE_PARAMETERS = {"loan_overdue_band"}  # stored as min_months/max_months; their `value` is empty
TRUE_VALUES = {"true", "yes", "1", "y"}
LOAN_CATEGORIES = ("pass", "watchlist", "substandard", "doubtful", "loss")


class ParametersUnavailable(Exception):
    """Raised when verified parameters for the requested fiscal year are missing or inconsistent."""


@dataclass(frozen=True)
class Param:
    """One verified parameter row."""

    parameter: str
    category: str
    value: Decimal
    unit: str
    min_months: Decimal | None
    max_months: Decimal | None
    family: str
    source: str
    amended_by: str


def _dec(value: str | None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None


def _valid(row: dict, fy: int) -> bool:
    start, end = parse_fy(row.get("effective_from_fy")), parse_fy(row.get("effective_to_fy"))
    return (start is None or start <= fy) and (end is None or end >= fy)


class ParameterTable:
    """Cached reader for bank_parameters.csv (reloaded when the file changes)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._rows: list[dict] = []
        self._lock = threading.Lock()

    def rows(self) -> list[dict]:
        with self._lock:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
            if mtime != self._mtime:
                self._mtime = mtime
                self._rows = []
                if mtime is not None:
                    with self.path.open(encoding="utf-8-sig", newline="") as fh:
                        self._rows = list(csv.DictReader(fh))
            return list(self._rows)

    def verified(self, parameter: str, fiscal_year: str, family: str) -> list[Param]:
        """Verified rows of one parameter in force in `fiscal_year` for `family` ("general" rows apply to all)."""
        fy = parse_fy(fiscal_year)
        if fy is None:
            raise ParametersUnavailable(f"Invalid fiscal year {fiscal_year!r}.")
        out: list[Param] = []
        in_force = [r for r in self.rows() if r.get("parameter") == parameter and _valid(r, fy)]
        in_force = [r for r in in_force if r.get("family") == family] or [r for r in in_force if r.get("family") == "general"]
        # The newest edition in force decides, verified or not: an older edition's verified figures must never
        # stand in for a newer edition whose figures are not verified yet (they may differ, e.g. 1.10% vs 1%).
        newest = max((parse_fy(r.get("effective_from_fy")) or 0 for r in in_force), default=None)
        own = [r for r in in_force if (r.get("verified") or "").strip().lower() in TRUE_VALUES]
        for r in own:
            if (parse_fy(r.get("effective_from_fy")) or 0) != newest:
                continue
            value = _dec(r.get("value"))
            if value is None and parameter in RANGE_PARAMETERS:
                value = Decimal(0)  # unused for ranges (kept so Param.value stays a number)
            if value is None:
                continue
            out.append(Param(
                parameter=parameter, category=r.get("category") or "", value=value, unit=r.get("unit") or "",
                min_months=_dec(r.get("min_months")), max_months=_dec(r.get("max_months")), family=r.get("family") or "",
                source=f"{r.get('source_label')}, p.{r.get('page')}", amended_by=r.get("amended_by") or "",
            ))
        if not out:
            raise ParametersUnavailable(
                f"The figures this calculator needs for FY {fiscal_year} have not been checked against the NRB "
                "documents yet, so it cannot calculate for this year. Please refer to the cited directive or notice."
            )
        return out

    def fiscal_years(self) -> dict[str, list[str]]:
        """Per calculator, the fiscal years (from verified rows' start years) it can compute for."""
        from app.fiscal_year import current_fy_start, fy_label

        out: dict[str, set[str]] = {"loan_provision": set(), "cash_limit": set()}
        for r in self.rows():
            if (r.get("verified") or "").strip().lower() not in TRUE_VALUES:
                continue
            start = parse_fy(r.get("effective_from_fy"))
            name = {"loan_loss_provision": "loan_provision", "cash_transaction_limit": "cash_limit"}.get(r.get("parameter", ""))
            if name and start:
                # open-ended rows (no effective_to_fy) stay valid up to the current fiscal year
                end = parse_fy(r.get("effective_to_fy")) or max(start + 1, current_fy_start())
                out[name] |= {fy_label(y) for y in range(start, end + 1)}
        return {k: sorted(v, reverse=True) for k, v in out.items()}

    def loan_provisioning(self, fiscal_year: str, family: str) -> dict[str, Param]:
        """Provisioning rate per loan category; all five categories must be verified."""
        rates = {p.category: p for p in self.verified("loan_loss_provision", fiscal_year, family)}
        missing = [c for c in LOAN_CATEGORIES if c not in rates]
        if missing:
            raise ParametersUnavailable(f"Verified provisioning rates missing for: {', '.join(missing)} (FY {fiscal_year}).")
        return rates

    def overdue_bands(self, fiscal_year: str, family: str) -> dict[str, Param]:
        """Overdue period (months) that defines each loan category."""
        bands = {p.category: p for p in self.verified("loan_overdue_band", fiscal_year, family)}
        missing = [c for c in LOAN_CATEGORIES if c not in bands]
        if missing:
            raise ParametersUnavailable(f"Verified overdue periods missing for: {', '.join(missing)} (FY {fiscal_year}).")
        return bands

    def cash_limit(self, fiscal_year: str) -> Param:
        rows = self.verified("cash_transaction_limit", fiscal_year, "general")
        values = {p.value for p in rows}
        if len(values) != 1:
            raise ParametersUnavailable(f"Conflicting verified cash-transaction limits for FY {fiscal_year}: {sorted(values)}.")
        return rows[0]
