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


def legacy_ga4_csv_name(
    file_stem: str,
    today: date | None = None,
    months_back: int = 1,
) -> str:
    """Return the pre-folder filename for the legacy flat-file migration.

    Reports such as the events summary used to be written directly under the
    provider folder (``events_YYYY-MM.csv``) before moving into a subfolder
    (``events/events_YYYY-MM.csv``). The legacy name drops the subfolder so an
    existing Drive file can be moved instead of being refetched.
    """
    month_start = month_range(today, months_back=months_back)[0]
    flat_stem = file_stem.rsplit("/", 1)[-1]
    return f"{flat_stem}_{month_start:%Y-%m}.csv"


def serialize_ga4_rows(
    rows: list[dict[str, Any]] | None,
    report: GA4Report,
) -> bytes:
    fieldnames = [*report.dimension_columns, *report.metric_columns]
    records = [_flatten_ga4_row(row, report) for row in rows or []]
    return serialize_dict_rows(records, fieldnames)


def _flatten_ga4_row(
    row: dict[str, Any],
    report: GA4Report,
) -> dict[str, Any]:
    dimension_values = row.get("dimensionValues") or []
    metric_values = row.get("metricValues") or []

    record: dict[str, Any] = {
        column: (
            dimension_values[index].get("value", "")
            if index < len(dimension_values)
            else ""
        )
        for index, column in enumerate(report.dimension_columns)
    }
    for column, metric_value in zip(report.metric_columns, metric_values):
        record[column] = metric_value.get("value", "")
    return record
