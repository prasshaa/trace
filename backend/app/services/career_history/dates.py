"""Partial-date arithmetic, identical to trace_model.py and the trace_util SQL helpers.

A month is an integer index: year * 12 + (month - 1). A partial date is a closed interval
[lo, hi] of month indices:
  month    lo == hi
  year     lo = January, hi = December of that year
  unknown  lo = hi = None
  ongoing  lo = hi = None (end dates only; "Present" as stated at resume_as_of)
Employment months are inclusive: 2021-06 to 2023-03 occupies both June 2021 and March 2023.
"""
from typing import Literal, Optional, Tuple

DatePrecision = Literal["month", "year", "unknown", "ongoing"]

MIN_YEAR = 1950  # trace_util.valid_partial_date
MAX_YEAR = 2100


def valid_partial_date(year: Optional[int], month: Optional[int], precision: str) -> bool:
    """Mirror of trace_util.valid_partial_date."""
    if precision == "month":
        return year is not None and month is not None and MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12
    if precision == "year":
        return year is not None and month is None and MIN_YEAR <= year <= MAX_YEAR
    if precision in ("unknown", "ongoing"):
        return year is None and month is None
    return False


def month_lo(year: Optional[int], month: Optional[int], precision: str) -> Optional[int]:
    if precision == "month":
        return year * 12 + month - 1
    if precision == "year":
        return year * 12
    return None


def month_hi(year: Optional[int], month: Optional[int], precision: str) -> Optional[int]:
    if precision == "month":
        return year * 12 + month - 1
    if precision == "year":
        return year * 12 + 11
    return None


def bounds(year: Optional[int], month: Optional[int], precision: str) -> Tuple[Optional[int], Optional[int]]:
    return month_lo(year, month, precision), month_hi(year, month, precision)


def feasible_period(start: Tuple[Optional[int], Optional[int], str],
                    end: Tuple[Optional[int], Optional[int], str]) -> bool:
    """Mirror of trace_util.feasible_period: the latest possible end is not before the
    earliest possible start. Unknown or ongoing ends are always feasible."""
    end_hi = month_hi(*end)
    start_lo = month_lo(*start)
    if end_hi is None or start_lo is None:
        return True
    return end_hi >= start_lo


def month_label(index: Optional[int]) -> Optional[str]:
    if index is None:
        return None
    return f"{index // 12:04d}-{index % 12 + 1:02d}"
