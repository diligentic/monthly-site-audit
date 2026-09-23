import csv
import os
from datetime import date
from pathlib import Path
from typing import Any

from constants.sites import Site
from constants.sources import Provider
from utils.dates import month_range

WEB_CORE_VITALS_COLUMNS = ("device", "lcp_ms", "inp_ms", "cls")


def web_core_vitals_csv_path(
    site: Site, today: date | None = None, months_back: int = 1
) -> Path:
    month_start = month_range(today, months_back=months_back)[0]
    return (
        site.provider_dir(Provider.WEB_CORE_VITALS)
        / f"web_core_vitals_{month_start:%Y-%m}.csv"
    )


def write_web_core_vitals_rows_to_csv(
    site: Site,
    rows: list[dict[str, Any]] | None,
    today: date | None = None,
    months_back: int = 1,
) -> Path:
    csv_path = web_core_vitals_csv_path(site, today, months_back)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = csv_path.with_name(f"{csv_path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=WEB_CORE_VITALS_COLUMNS)
            writer.writeheader()
            for row in rows or []:
                writer.writerow(
                    {
                        column: row.get(column, "")
                        for column in WEB_CORE_VITALS_COLUMNS
                    }
                )
        os.replace(temp_path, csv_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return csv_path
