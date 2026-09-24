from datetime import date
from typing import Any

from constants.search_console import country_file_prefix
from utils.csv_serializer import serialize_metrics_rows
from utils.dates import month_range


def pages_csv_name(
    today: date | None = None,
    months_back: int = 1,
    *,
    country: str | None = None,
) -> str:
    month_start = month_range(today, months_back=months_back)[0]
    return f"{country_file_prefix(country)}pages_{month_start:%Y-%m}.csv"


def serialize_page_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_metrics_rows(rows, key_column="page")