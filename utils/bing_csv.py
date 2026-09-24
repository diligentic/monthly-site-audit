from datetime import date
from typing import Any

from utils.csv_serializer import serialize_metrics_rows
from utils.dates import month_range


def bing_queries_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    month_start = month_range(today, months_back=months_back)[0]
    return f"queries_{month_start:%Y-%m}.csv"


def bing_pages_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    month_start = month_range(today, months_back=months_back)[0]
    return f"pages_{month_start:%Y-%m}.csv"


def serialize_bing_query_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_metrics_rows(rows, key_column="query")


def serialize_bing_page_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_metrics_rows(rows, key_column="page")