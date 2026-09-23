from datetime import date
from pathlib import Path
from typing import Any

from constants.crawl import IMAGE_COLUMNS
from constants.sites import Site
from constants.sources import Provider
from utils.crawl_csv import _storage_root, _write_rows
from utils.dates import month_range


def image_csv_path(site: Site, today: date | None = None, months_back: int = 1) -> Path:
    month = month_range(today, months_back=months_back)[0]
    return _storage_root() / site.name / Provider.IMAGES.value / f"images_{month:%Y-%m}.csv"


def write_image_rows_to_csv(
    site: Site, rows: list[dict[str, Any]] | None,
    today: date | None = None, months_back: int = 1,
) -> Path:
    path = image_csv_path(site, today, months_back)
    _write_rows(path, IMAGE_COLUMNS, rows or [], {
        "image_url": "Image URL", "status": "Status Code", "content_type": "Content Type",
        "file_size": "File Size", "width": "Width", "height": "Height", "error": "Error",
    })
    return path
