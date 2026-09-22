from datetime import date
from pathlib import Path
from typing import Any

from constants.sites import Provider, Site
from utils.dates import month_range
from utils.metrics_csv import write_metrics_csv


def pages_csv_path(
    site: Site,
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    month_start = month_range(today, months_back=months_back)[0]
    return site.provider_dir(Provider.GSC) / f"pages_{month_start:%Y-%m}.csv"


def write_page_rows_to_csv(
    site: Site,
    rows: list[dict[str, Any]],
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    csv_path = pages_csv_path(site, today, months_back)
    return write_metrics_csv(rows, csv_path, key_column="page")
