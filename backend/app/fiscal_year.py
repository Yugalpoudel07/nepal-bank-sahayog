"""
fiscal_year.py
==============
Nepal fiscal-year utilities.

Nepal's fiscal year (आर्थिक वर्ष) runs from Shrawan 1 to the last day of Ashadh
in the Bikram Sambat (BS) calendar — roughly mid-July to mid-July in AD.
FY "2083/84" therefore starts on Shrawan 1, 2083 BS.

Internally a fiscal year is represented by its *start BS year* (an ``int``,
e.g. 2083). The display label is "2083/84".

Calendar conversion uses the maintained ``nepali-datetime`` library; nothing
about "today's" fiscal year is hard-coded.
"""

from __future__ import annotations

import datetime as dt
import re

import nepali_datetime

SHRAWAN = 4  # Baisakh=1, Jestha=2, Ashadh=3, Shrawan=4 ...

# BS year minus AD year for dates on/after Shrawan 1 (mid-July) — always 57.
AD_TO_BS_FY_OFFSET = 57

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_FY_LABEL_RE = re.compile(r"^\s*(\d{4})\s*[/\-–]\s*(\d{2}|\d{4})\s*$")


def to_ascii_digits(text: str) -> str:
    """Replace Devanagari digits (०-९) with ASCII digits."""
    return text.translate(_DEVANAGARI_DIGITS)


def ad_to_bs(day: dt.date) -> tuple[int, int, int]:
    """Convert an AD date to a BS (year, month, day) tuple."""
    nd = nepali_datetime.date.from_datetime_date(day)
    return nd.year, nd.month, nd.day


def bs_to_ad(year: int, month: int, day: int) -> dt.date:
    """Convert a BS date to an AD ``datetime.date``."""
    return nepali_datetime.date(year, month, day).to_datetime_date()


def fy_start_for_bs(year: int, month: int) -> int:
    """Fiscal-year start year for a BS (year, month)."""
    return year if month >= SHRAWAN else year - 1


def fy_start_for_date(day: dt.date) -> int:
    """Fiscal-year start year (BS) containing the given AD date."""
    y, m, _ = ad_to_bs(day)
    return fy_start_for_bs(y, m)


def fy_label(start: int) -> str:
    """2083 -> "2083/84"."""
    return f"{start}/{(start + 1) % 100:02d}"


def current_fy_start(today: dt.date | None = None) -> int:
    """Start year of the fiscal year in force today (Asia/Kathmandu date by default)."""
    if today is None:
        today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=45)).date()
    return fy_start_for_date(today)


def current_fy(today: dt.date | None = None) -> str:
    """Label of the fiscal year in force today, e.g. "2083/84"."""
    return fy_label(current_fy_start(today))


def fy_bounds_ad(start: int) -> tuple[dt.date, dt.date]:
    """First and last AD dates of a fiscal year."""
    first = bs_to_ad(start, SHRAWAN, 1)
    last = bs_to_ad(start + 1, SHRAWAN, 1) - dt.timedelta(days=1)
    return first, last


def parse_fy(value: str | int | None) -> int | None:
    """
    Parse a fiscal-year value into its BS start year.

    Accepts 2083, "2083", "2083/84", "2083-84", "2083/2084", "२०८३/८४".
    AD-style labels ("2026/27") are converted to BS (+57).
    Returns None for empty/invalid input.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = to_ascii_digits(str(value)).strip()
    if not text:
        return None
    m = _FY_LABEL_RE.match(text)
    if m:
        first, second = int(m.group(1)), m.group(2)
        nxt = int(second) % 100
        if nxt != (first + 1) % 100:
            return None
        return first + AD_TO_BS_FY_OFFSET if first < 2060 else first
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return year + AD_TO_BS_FY_OFFSET if year < 2060 else year
    return None


def normalize_fy_label(value: str | int | None) -> str | None:
    """Parse any FY form and return the canonical label, or None."""
    start = parse_fy(value)
    return fy_label(start) if start is not None else None


def fy_from_bs_date_string(value: str | None) -> int | None:
    """FY start for a BS date string like "2083-05-12" or "२०८३/०५/१२"."""
    if not value:
        return None
    m = re.match(r"^\s*(\d{4})\D(\d{1,2})\D(\d{1,2})", to_ascii_digits(str(value)))
    if not m:
        return None
    return fy_start_for_bs(int(m.group(1)), int(m.group(2)))
