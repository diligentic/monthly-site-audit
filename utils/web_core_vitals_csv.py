from datetime import date
from typing import Any

from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range

WEB_CORE_VITALS_COLUMNS = ("device", "lcp_ms", "inp_ms", "cls")


def web_core_vitals_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    month_start = month_range(today, months_back=months_back)[0]
    return f"web_core_vitals_{month_start:%Y-%m}.csv"


def serialize_web_core_vitals_rows(rows: list[dict[str, Any]] | None) -> bytes:
    records = [
        {column: row.get(column, "") for column in WEB_CORE_VITALS_COLUMNS}
        for row in rows or []
    ]
    return serialize_dict_rows(records, WEB_CORE_VITALS_COLUMNS)