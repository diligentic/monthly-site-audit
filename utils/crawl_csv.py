import csv
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from constants.crawl import CRAWL_COLUMNS, CRAWL_STORAGE_ROOT_ENV_VAR
from constants.sites import DATA_ROOT, Site
from constants.sources import Provider
from utils.dates import month_range
from utils.get_env import ConfigurationError


def _storage_root() -> Path:
    configured = os.getenv(CRAWL_STORAGE_ROOT_ENV_VAR, "").strip()
    if configured:
        return Path(configured)
    if sys.platform.startswith("linux"):
        raise ConfigurationError(
            f"Set {CRAWL_STORAGE_ROOT_ENV_VAR} to a persistent disk mount on Linux."
        )
    return DATA_ROOT


def crawl_csv_path(site: Site, today: date | None = None, months_back: int = 1) -> Path:
    month = month_range(today, months_back=months_back)[0]
    return _storage_root() / site.name / Provider.CRAWL.value / f"{month:%Y-%m}.csv"


def write_crawl_rows_to_csv(
    site: Site, rows: list[dict[str, Any]] | None,
    today: date | None = None, months_back: int = 1,
) -> Path:
    path = crawl_csv_path(site, today, months_back)
    _write_rows(path, CRAWL_COLUMNS, rows or [], {
        "url": "URL", "final_url": "Final URL", "status": "Status Code",
        "final_status": "Final Status Code",
        "reason": "Reason", "content_type": "Content Type",
        "indexability": "Indexability", "indexability_status": "Indexability Status",
        "canonical": "Canonical Link Element 1", "unique_inlinks": "Unique Inlinks",
        "redirect_url": "Redirect URL", "crawl_truncated": "Crawl Truncated",
    })
    return path


def _write_rows(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]], names: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({names.get(key, key): value for key, value in row.items()})
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
