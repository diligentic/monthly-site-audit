"""Filename and CSV serialisation for the Search Console URL Inspection report.

The report is written to Google Drive as
``<Site>/GSC/url_inspection_YYYY-MM.csv``, matching the flat, month-suffixed
layout of the other Search Console exports rather than the crawl reports'
``YYYY-MM/`` folders: it is a point-in-time snapshot of indexing, not a
per-month series of measurements.
"""

from datetime import date
from typing import Any

from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range

#: Column order of the report. It leads with the URL, then Google's verdict
#: and coverage state (what to act on), then the supporting states and
#: canonicals (why), and ends with the link back into the Search Console UI.
URL_INSPECTION_COLUMNS: tuple[str, ...] = (
    "URL",
    "Verdict",
    "Coverage state",
    "Robots.txt state",
    "Indexing state",
    "Page fetch state",
    "Last crawl time",
    "Google canonical",
    "User canonical",
    "Crawled as",
    "Referring URLs",
    "Google inspection link",
    "Error",
)


def url_inspection_csv_name(
    today: date | None = None,
    months_back: int = 1,
) -> str:
    """Return the URL Inspection filename for the month, e.g. ``url_inspection_2026-09.csv``."""
    month_start = month_range(today, months_back=months_back)[0]
    return f"url_inspection_{month_start:%Y-%m}.csv"


def serialize_url_inspection_rows(rows: list[dict[str, Any]] | None) -> bytes:
    """Encode URL Inspection rows as UTF-8 CSV bytes."""
    return serialize_dict_rows(rows, URL_INSPECTION_COLUMNS)
