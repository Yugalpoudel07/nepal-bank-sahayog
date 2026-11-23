"""Fiscal-year conversion, Shrawan-1 boundaries and FY detection in questions."""

from __future__ import annotations

import datetime as dt

import pytest

from app.fiscal_year import (
    ad_to_bs,
    bs_to_ad,
    current_fy,
    fy_bounds_ad,
    fy_from_bs_date_string,
    fy_label,
    fy_start_for_date,
    normalize_fy_label,
    parse_fy,
)
from app.fy_detect import detect_fy

TODAY = dt.date(2026, 11, 21)  # Mangsir 2083 -> FY 2083/84


def test_known_conversion() -> None:
    # Shrawan 1, 2082 BS fell on 17 July 2025 AD.
    assert bs_to_ad(2082, 4, 1) == dt.date(2025, 7, 17)
    assert ad_to_bs(dt.date(2025, 7, 17)) == (2082, 4, 1)


@pytest.mark.parametrize("bs_year", [2080, 2081, 2082, 2083, 2084])
def test_shrawan_boundary(bs_year: int) -> None:
    shrawan_1 = bs_to_ad(bs_year, 4, 1)
    assert fy_start_for_date(shrawan_1) == bs_year
    assert fy_start_for_date(shrawan_1 - dt.timedelta(days=1)) == bs_year - 1  # last day of Ashadh
    assert fy_start_for_date(shrawan_1 + dt.timedelta(days=1)) == bs_year


def test_current_fy_is_computed() -> None:
    assert current_fy(TODAY) == "2083/84"
    assert current_fy(dt.date(2026, 7, 16)) == "2082/83"
    assert current_fy(dt.date(2026, 7, 17)) == "2083/84"


def test_fy_bounds() -> None:
    first, last = fy_bounds_ad(2082)
    assert first == dt.date(2025, 7, 17)
    assert last == bs_to_ad(2083, 4, 1) - dt.timedelta(days=1)


def test_labels_and_parsing() -> None:
    assert fy_label(2083) == "2083/84"
    assert fy_label(2099) == "2099/00"
    for value in ("2083/84", "2083-84", "2083/2084", "२०८३/८४", 2083, "2083"):
        assert parse_fy(value) == 2083
    assert parse_fy("2025/26") == 2082  # AD fiscal-year label -> BS
    assert parse_fy("2083/85") is None
    assert parse_fy("") is None
    assert normalize_fy_label("2081-82") == "2081/82"
    assert fy_from_bs_date_string("2083-03-31") == 2082  # Ashadh -> previous FY
    assert fy_from_bs_date_string("२०८३-०४-०१") == 2083  # Shrawan 1


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What was the VAT threshold in 2081/82?", 2081),
        ("Income tax slabs FY 2080-81", 2080),
        ("Rates for fiscal year 2025/26", 2082),
        ("आर्थिक वर्ष २०८२/८३ को आयकर दर के हो?", 2082),
        ("What changed in the Finance Act 2083?", 2083),
        ("आर्थिक ऐन २०८२ मा के परिवर्तन भयो?", 2082),
        ("What were the rates last year?", 2082),
        ("Rates for this fiscal year", 2083),
        ("गत आर्थिक वर्षको दर कति थियो?", 2082),
        ("Compare 2081/82 and 2082/83 slabs", 2082),
        ("FY 2081 VAT rules", 2081),
    ],
)
def test_detect_fy(question: str, expected: int) -> None:
    found = detect_fy(question, today=TODAY)
    assert found is not None and found.start == expected


@pytest.mark.parametrize("question", ["What is the VAT registration threshold?", "Income Tax Act 2058 section 10",
                                      "Fee under VAT Act 2052", "Due date 2083-05-12"])
def test_no_fy(question: str) -> None:
    assert detect_fy(question, today=TODAY) is None


def test_detect_fy_lists_all_mentioned() -> None:
    found = detect_fy("from 2081/82 to 2083/84", today=TODAY)
    assert found is not None and found.mentioned == [2081, 2083]
