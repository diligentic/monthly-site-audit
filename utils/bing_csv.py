from datetime import date
from pathlib import Path
from typing import Any

from constants.sites import Site
from constants.sources import Provider
from utils.dates import month_range
from utils.metrics_csv import write_metrics_csv


def bing_queries_csv_path(
    site: Site, today: date | None = None, months_back: int = 1
) -> Path:
    month_start = month_range(today, months_back=months_back)[0]
    return site.provider_dir(Provider.BING) / f"queries_{month_start:%Y-%m}.csv"


def bing_pages_csv_path(
    site: Site, today: date | None = None, months_back: int = 1
) -> Path:
    month_start = month_range(today, months_back=months_back)[0]
    return site.provider_dir(Provider.BING) / f"pages_{month_start:%Y-%m}.csv"


def write_bing_query_rows_to_csv(
    site: Site,
    rows: list[dict[str, Any]],
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    return write_metrics_csv(
        rows,
        bing_queries_csv_path(site, today, months_back),
        key_column="query",
    )


def write_bing_page_rows_to_csv(
    site: Site,
    rows: list[dict[str, Any]],
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    return write_metrics_csv(
        rows,
        bing_pages_csv_path(site, today, months_back),
        key_column="page",
    )
