from datetime import date
from typing import Any

from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range

SITEMAP_COLUMNS = ("url", "lastmod", "changefreq", "priority")


def sitemap_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    month_start = month_range(today, months_back=months_back)[0]
    return f"sitemap_{month_start:%Y-%m}.csv"


def serialize_sitemap_rows(rows: list[dict[str, Any]] | None) -> bytes:
    records = [
        {column: row.get(column, "") for column in SITEMAP_COLUMNS}
        for row in rows or []
    ]
    return serialize_dict_rows(records, SITEMAP_COLUMNS)