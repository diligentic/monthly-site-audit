import csv
import os
from datetime import date
from pathlib import Path
from typing import Any

from constants.ga4 import GA4_KEY_COLUMN, GA4_METRIC_COLUMNS
from constants.sites import Provider, Site
from utils.dates import month_range

GA4_CSV_BASENAME = "traffic_acquisition"


def ga4_csv_path(
    site: Site,
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    month_start = month_range(today, months_back=months_back)[0]
    return (
        site.provider_dir(Provider.GA4) / f"{GA4_CSV_BASENAME}_{month_start:%Y-%m}.csv"
    )


def write_ga4_rows_to_csv(
    site: Site,
    rows: list[dict[str, Any]] | None,
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    csv_path = ga4_csv_path(site, today, months_back)
    fieldnames = [GA4_KEY_COLUMN, *GA4_METRIC_COLUMNS]
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = csv_path.with_name(f"{csv_path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows or []:
                writer.writerow(_flatten_ga4_row(row))
        os.replace(temp_path, csv_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return csv_path


def _flatten_ga4_row(row: dict[str, Any]) -> dict[str, Any]:
    dimension_values = row.get("dimensionValues") or []
    metric_values = row.get("metricValues") or []

    record: dict[str, Any] = {
        GA4_KEY_COLUMN: (
            dimension_values[0].get("value", "") if dimension_values else ""
        )
    }
    for column, metric_value in zip(GA4_METRIC_COLUMNS, metric_values):
        record[column] = metric_value.get("value", "")
    return record
