from datetime import date
from typing import Any

from constants.crawl import CRAWL_COLUMNS, SCREAMING_FROG_EXPORTS
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


def _screaming_frog_export_stem(export: str) -> str:
    try:
        _, stem = SCREAMING_FROG_EXPORTS[export]
    except KeyError as error:
        choices = ", ".join(sorted(SCREAMING_FROG_EXPORTS))
        raise ValueError(
            f"Unknown Screaming Frog export {export!r}; choose one of {choices}."
        ) from error
    return stem


def screaming_frog_csv_name(
    export: str,
    today: date | None = None,
    months_back: int = 1,
) -> str:
    """Return a Screaming Frog filename for use inside a month folder.

    ``today`` and ``months_back`` remain accepted for compatibility with the
    stream naming callback; the enclosing folder carries the month instead.
    """
    del today, months_back
    return f"{_screaming_frog_export_stem(export)}.csv"


def legacy_screaming_frog_csv_name(
    export: str,
    today: date | None = None,
    months_back: int = 1,
) -> str:
    """Return the pre-folder filename for a legacy flat-file migration."""
    month = month_range(today, months_back=months_back)[0]
    return f"{_screaming_frog_export_stem(export)}_{month:%Y-%m}.csv"


def internal_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    return screaming_frog_csv_name("internal", today, months_back)


def h1_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    return screaming_frog_csv_name("h1", today, months_back)


def meta_description_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    return screaming_frog_csv_name("meta_description", today, months_back)


def page_titles_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    return screaming_frog_csv_name("page_titles", today, months_back)


def images_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    return screaming_frog_csv_name("images", today, months_back)


def issues_crawl_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    return screaming_frog_csv_name("issues", today, months_back)


def raw_csv_bytes(content: bytes) -> bytes:
    """Pass through bytes that are already a complete CSV (Screaming Frog export)."""
    return content


def serialize_crawl_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_dict_rows(
        rows, CRAWL_COLUMNS, names=_CRAWL_HEADER_NAMES
    )