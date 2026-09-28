"""Column schemas, filenames, and CSV serialisation for the crawl reports.

Each report is written to Google Drive as
``<Site>/Crawls/YYYY-MM/<report>.csv``; the enclosing month folder carries the
date, so the file names are stable across months.
"""

from datetime import date
from typing import Any

from constants.crawl import CRAWL_EXPORTS
from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range

INTERNAL_COLUMNS: tuple[str, ...] = (
    "URL",
    "Status Code",
    "Indexability",
    "Indexability Status",
    "Indexability Reason",
    "Canonical URL",
    "Inlinks",
    "Unique Inlinks",
    "Outlinks",
    "Unique Outlinks",
    "External Outlinks",
    "Word Count",
    "Content Type",
    "Crawl Depth",
    "Title",
    "Redirect target",
    "In Sitemap",
)

H1_COLUMNS: tuple[str, ...] = ("URL", "H1", "H1 count", "H1 length")

META_DESCRIPTION_COLUMNS: tuple[str, ...] = (
    "URL",
    "Meta Description",
    "Meta Description length",
    "Missing Meta Description",
)

PAGE_TITLE_COLUMNS: tuple[str, ...] = (
    "URL",
    "Page Title",
    "Page Title length",
    "Missing Page Title",
)

ISSUE_COLUMNS: tuple[str, ...] = ("URL", "Issue", "Category", "Severity", "Details")

CRAWL_COLUMNS: dict[str, tuple[str, ...]] = {
    "internal": INTERNAL_COLUMNS,
    "h1": H1_COLUMNS,
    "meta_description": META_DESCRIPTION_COLUMNS,
    "page_titles": PAGE_TITLE_COLUMNS,
    "issues": ISSUE_COLUMNS,
}


def _export_stem(export: str) -> str:
    try:
        _, stem = CRAWL_EXPORTS[export]
    except KeyError as error:
        choices = ", ".join(sorted(CRAWL_EXPORTS))
        raise ValueError(
            f"Unknown crawl export {export!r}; choose one of {choices}."
        ) from error
    return stem


def crawl_csv_name(export: str, today: date | None = None, months_back: int = 1) -> str:
    """Return the crawl filename for use inside a month folder.

    ``today`` and ``months_back`` are accepted to match the stream naming
    callback; the enclosing folder carries the month instead.
    """
    del today, months_back
    return f"{_export_stem(export)}.csv"


def legacy_crawl_csv_name(
    export: str, today: date | None = None, months_back: int = 1
) -> str:
    """Return the pre-folder filename for the legacy flat-file migration."""
    month = month_range(today, months_back=months_back)[0]
    return f"{_export_stem(export)}_{month:%Y-%m}.csv"


def serialize_crawl_rows(
    rows: list[dict[str, Any]] | None, export: str
) -> bytes:
    """Encode the rows of one crawl report as UTF-8 CSV bytes."""
    return serialize_dict_rows(rows, CRAWL_COLUMNS[export])
