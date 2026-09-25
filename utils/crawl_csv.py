from datetime import date

from constants.crawl import SCREAMING_FROG_EXPORTS
from utils.dates import month_range


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


def raw_csv_bytes(content: bytes) -> bytes:
    """Pass through bytes that are already a complete CSV (Screaming Frog export)."""
    return content
