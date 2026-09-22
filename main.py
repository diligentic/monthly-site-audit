import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from constants.ga4 import GA4_REPORTS, GA4Report
from constants.search_console import COUNTRY_FILTER_EXPRESSION
from constants.sites import (
    QUARTERLY_MONTHS,
    SITES,
    Schedule,
    Site,
)
from constants.sources import Provider
from services.ga4 import fetch_ga4_data, validate_ga4_credentials
from services.search_console import (
    fetch_page_data,
    fetch_query_data,
    validate_credentials,
)
from utils.dates import month_range
from utils.ga4_csv import ga4_csv_path, write_ga4_rows_to_csv
from utils.pages_csv import pages_csv_path, write_page_rows_to_csv
from utils.queries_csv import queries_csv_path, write_query_rows_to_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stream:
    label: str
    fetch: Callable[..., dict[str, Any]]
    csv_path: Callable[..., Path]
    write_rows: Callable[..., Path]


def ga4_stream(site: Site, report: GA4Report) -> Stream:
    return Stream(
        label=f"{site.name} {Provider.GA4.value} {report.label}",
        fetch=partial(
            fetch_ga4_data,
            property_id_env_var=site.ga4_property_id_env_var,
            report=report,
        ),
        csv_path=partial(ga4_csv_path, site, report),
        write_rows=partial(write_ga4_rows_to_csv, site, report),
    )


def streams_for(site: Site) -> tuple[Stream, ...]:
    provider = Provider.GSC
    return (
        Stream(
            label=f"{site.name} {provider.value} query",
            fetch=partial(fetch_query_data, site_url=site.gsc_site_url),
            csv_path=partial(queries_csv_path, site),
            write_rows=partial(write_query_rows_to_csv, site),
        ),
        Stream(
            label=f"{site.name} {provider.value} page",
            fetch=partial(fetch_page_data, site_url=site.gsc_site_url),
            csv_path=partial(pages_csv_path, site),
            write_rows=partial(write_page_rows_to_csv, site),
        ),
        Stream(
            label=f"{site.name} {provider.value} Canada query",
            fetch=partial(
                fetch_query_data,
                site_url=site.gsc_site_url,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
            csv_path=partial(
                queries_csv_path,
                site,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
            write_rows=partial(
                write_query_rows_to_csv,
                site,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
        ),
        Stream(
            label=f"{site.name} {provider.value} Canada page",
            fetch=partial(
                fetch_page_data,
                site_url=site.gsc_site_url,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
            csv_path=partial(
                pages_csv_path,
                site,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
            write_rows=partial(
                write_page_rows_to_csv,
                site,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
        ),
        *[ga4_stream(site, report) for report in GA4_REPORTS],
    )


def ensure_month_data(
    stream: Stream,
    today: date,
    months_back: int,
    *,
    overwrite: bool,
) -> int:
    month_start, month_end = month_range(today, months_back)
    csv_path = stream.csv_path(today, months_back)

    if csv_path.exists() and not overwrite:
        logger.info(
            "Skipping %s (%s): already present at %s",
            month_start.strftime("%Y-%m"),
            stream.label,
            csv_path,
        )
        return 0

    response = stream.fetch(start_date=month_start, end_date=month_end)
    rows = response.get("rows") or []
    stream.write_rows(rows, today, months_back)
    logger.info(
        "Stored %d %s rows for %s in %s",
        len(rows),
        stream.label,
        month_start.strftime("%Y-%m"),
        csv_path,
    )
    return len(rows)


def run_site(site: Site, today: date) -> int:
    failures = 0
    for months_back in range(1, site.history_months + 1):
        overwrite = months_back <= site.always_fetch_months
        month_start = month_range(today, months_back)[0].strftime("%Y-%m")
        for stream in streams_for(site):
            try:
                ensure_month_data(stream, today, months_back, overwrite=overwrite)
            except (OSError, RuntimeError) as error:
                failures += 1
                logger.error(
                    "Failed to collect %s for %s: %s",
                    stream.label,
                    month_start,
                    error,
                )
    return failures


def is_due(site: Site, today: date) -> bool:
    return site.schedule is not Schedule.QUARTERLY or today.month in QUARTERLY_MONTHS


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Search Console and GA4 performance data on a monthly/quarterly schedule."
    )
    parser.add_argument(
        "--site",
        choices=[site.name for site in SITES],
        help="Audit only this site instead of all scheduled sites.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Anchor date; defaults to today, e.g. 2026-07-15 to simulate a July run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    load_dotenv()
    validate_credentials()
    validate_ga4_credentials(SITES)
    today = args.date or datetime.now(UTC).date()

    failures = 0
    for site in SITES:
        if args.site is not None and site.name != args.site:
            continue
        if not is_due(site, today):
            logger.info(
                "Skipping %s: quarterly audit runs in %s",
                site.name,
                ", ".join(str(month) for month in sorted(QUARTERLY_MONTHS)),
            )
            continue
        failures += run_site(site, today)

    if failures:
        raise SystemExit(1)
    logger.info("Audit complete.")


if __name__ == "__main__":
    main()
