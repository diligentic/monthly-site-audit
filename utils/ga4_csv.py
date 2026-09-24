from datetime import date
from typing import Any

from constants.ga4 import GA4Report
from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range


def ga4_csv_name(
    report: GA4Report,
    today: date | None = None,
    months_back: int = 1,
) -> str:
    month_start = month_range(today, months_back=months_back)[0]
    return f"{report.file_stem}_{month_start:%Y-%m}.csv"


def serialize_ga4_rows(
    rows: list[dict[str, Any]] | None,
    report: GA4Report,
) -> bytes:
    fieldnames = [report.key_column, *report.metric_columns]
    records = [_flatten_ga4_row(row, report) for row in rows or []]
    return serialize_dict_rows(records, fieldnames)


def _flatten_ga4_row(
    row: dict[str, Any],
    report: GA4Report,
) -> dict[str, Any]:
    dimension_values = row.get("dimensionValues") or []
    metric_values = row.get("metricValues") or []

    record: dict[str, Any] = {
        report.key_column: (
            dimension_values[0].get("value", "") if dimension_values else ""
        )
    }
    for column, metric_value in zip(report.metric_columns, metric_values):
        record[column] = metric_value.get("value", "")
    return record
