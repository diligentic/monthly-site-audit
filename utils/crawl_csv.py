from datetime import date
from typing import Any

from constants.crawl import CRAWL_COLUMNS
from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range

_CRAWL_HEADER_NAMES = {
    "url": "URL",
    "final_url": "Final URL",
    "status": "Status Code",
    "final_status": "Final Status Code",
    "reason": "Reason",
    "content_type": "Content Type",
    "indexability": "Indexability",
    "indexability_status": "Indexability Status",
    "canonical": "Canonical Link Element 1",
    "unique_inlinks": "Unique Inlinks",
    "redirect_url": "Redirect URL",
    "crawl_truncated": "Crawl Truncated",
}


def crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    month = month_range(today, months_back=months_back)[0]
    return f"{month:%Y-%m}.csv"


def serialize_crawl_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_dict_rows(
        rows, CRAWL_COLUMNS, names=_CRAWL_HEADER_NAMES
    )