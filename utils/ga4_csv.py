import csv
import os
from datetime import date
from pathlib import Path
from typing import Any

from constants.ga4 import GA4Report
from constants.sites import Provider, Site
from utils.dates import month_range


def ga4_csv_path(
    site: Site,
    report: GA4Report,
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    month_start = month_range(today, months_back=months_back)[0]
    return (
        site.provider_dir(Provider.GA4) / f"{report.file_stem}_{month_start:%Y-%m}.csv"
    )


def write_ga4_rows_to_csv(
    site: Site,
    report: GA4Report,
    rows: list[dict[str, Any]] | None,
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    csv_path = ga4_csv_path(site, report, today, months_back)
    fieldnames = [report.key_column, *report.metric_columns]
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = csv_path.with_name(f"{csv_path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows or []:
                writer.writerow(_flatten_ga4_row(row, report))
        os.replace(temp_path, csv_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return csv_path


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
