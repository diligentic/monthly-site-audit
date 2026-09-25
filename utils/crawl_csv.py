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


def internal_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    """Google Drive name for the Screaming Frog crawl, e.g. ``internal_2026-09.csv``.

    The name is independent of Screaming Frog's own export filename
    (``internal_all.csv``).
    """
    month = month_range(today, months_back=months_back)[0]
    return f"internal_{month:%Y-%m}.csv"


def issues_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    """Google Drive name for the Screaming Frog issues export, e.g. ``issues_2026-09.csv``.

    Independent of Screaming Frog's own export filename (``issues_all.csv``).
    """
    month = month_range(today, months_back=months_back)[0]
    return f"issues_{month:%Y-%m}.csv"


def raw_csv_bytes(content: bytes) -> bytes:
    """Pass through bytes that are already a complete CSV (Screaming Frog export)."""
    return content


def serialize_crawl_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_dict_rows(
        rows, CRAWL_COLUMNS, names=_CRAWL_HEADER_NAMES
    )