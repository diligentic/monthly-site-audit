from datetime import UTC, date, datetime, timedelta


def _month_key(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _month_from_key(key: int) -> tuple[int, int]:
    year, zero_based_month = divmod(key, 12)
    return year, zero_based_month + 1


def _shift_month(today: date, months_back: int) -> date:
    year, month = _month_from_key(_month_key(today.year, today.month) - months_back)
    return date(year, month, 1)


def _last_day_of_month(month_start: date) -> date:
    next_month_start = _shift_month(month_start, months_back=-1)
    return next_month_start - timedelta(days=1)


def month_range(
    today: date | None = None,
    months_back: int = 1,
) -> tuple[date, date]:
    anchor = today or datetime.now(UTC).date()
    month_start = _shift_month(anchor, months_back)
    return month_start, _last_day_of_month(month_start)


def previous_calendar_month(today: date | None = None) -> tuple[date, date]:
    return month_range(today, months_back=1)
