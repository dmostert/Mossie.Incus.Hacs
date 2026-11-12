"""Go: shared/util (the small, portable helpers)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_TRUE = ("true", "1", "yes", "on")
_FALSE = ("false", "0", "no", "off")


def is_true(value: str) -> bool:
    return value.lower() in _TRUE


def is_false(value: str) -> bool:
    return value.lower() in _FALSE


def is_true_or_empty(value: str) -> bool:
    return value == "" or is_true(value)


def is_user_config(key: str) -> bool:
    return key.startswith("user.")


def string_prefix_in_slice(key: str, items) -> bool:
    return any(key.startswith(s) for s in items)


def _add_months(d: datetime, n: int) -> datetime:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return d.replace(year=year, month=month, day=day)


def get_expiry(ref_date: datetime, expression: str) -> datetime | None:
    """Go: GetExpiry. Parses "1S,2M,3H,4d,5w,6m,7y" (m = months, M = minutes) relative to ``ref_date``. Empty -> None."""
    expression = expression.strip()
    if not expression:
        return None
    result = ref_date
    for part in (p.strip() for p in expression.split(",") if p.strip()):
        m = re.fullmatch(r"(\d+)(S|M|H|d|w|m|y)", part)
        if not m:
            raise ValueError(f"Invalid expiry expression: {part}")
        n, unit = int(m.group(1)), m.group(2)
        result = {"S": lambda: result + timedelta(seconds=n), "M": lambda: result + timedelta(minutes=n),
                  "H": lambda: result + timedelta(hours=n), "d": lambda: result + timedelta(days=n),
                  "w": lambda: result + timedelta(weeks=n), "m": lambda: _add_months(result, n),
                  "y": lambda: _add_months(result, 12 * n)}[unit]()
    return result
