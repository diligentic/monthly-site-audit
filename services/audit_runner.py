import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any

from constants.ga4 import GA4_REPORTS, GA4Report
from constants.search_console import COUNTRY_FILTER_EXPRESSION
from constants.sites import (
    QUARTERLY_MONTHS,
    SITES,
    Schedule,
    Site,
)
from constants.sources import Provider
from services.bing import (
    fetch_page_data as fetch_bing_page_data,
)
from services.bing import (
    fetch_query_data as fetch_bing_query_data,
)
from services.bing import (
    validate_bing_credentials,
)
from services.crawl import clear_crawl_cache, fetch_crawl_data
from services.ga4 import fetch_ga4_data, validate_ga4_credentials
from services.search_console import (
    fetch_page_data,
    fetch_query_data,
    validate_credentials,
)
from services.sitemap import fetch_sitemap_data
from services.web_core_vitals import fetch_web_core_vitals_data
from utils.bing_csv import (
    bing_pages_csv_path,
    bing_queries_csv_path,
    write_bing_page_rows_to_csv,
    write_bing_query_rows_to_csv,
)
from utils.crawl_csv import crawl_csv_path, write_crawl_rows_to_csv
from utils.dates import month_range
from utils.ga4_csv import ga4_csv_path, write_ga4_rows_to_csv
from utils.image_csv import image_csv_path, write_image_rows_to_csv
from utils.pages_csv import pages_csv_path, write_page_rows_to_csv
from utils.queries_csv import queries_csv_path, write_query_rows_to_csv
from utils.sitemap_csv import sitemap_csv_path, write_sitemap_rows_to_csv
from utils.web_core_vitals_csv import (
    web_core_vitals_csv_path,
    write_web_core_vitals_rows_to_csv,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stream:
    label: str
    fetch: Callable[..., dict[str, Any]]
    csv_path: Callable[..., Path]
    write_rows: Callable[..., Path]
    response_field: str = "rows"


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
        Stream(
            label=f"{site.name} {Provider.BING.value} query",
            fetch=partial(fetch_bing_query_data, site_url=site.bing_site_url),
            csv_path=partial(bing_queries_csv_path, site),
            write_rows=partial(write_bing_query_rows_to_csv, site),
        ),
        Stream(
            label=f"{site.name} {Provider.BING.value} page",
            fetch=partial(fetch_bing_page_data, site_url=site.bing_site_url),
            csv_path=partial(bing_pages_csv_path, site),
            write_rows=partial(write_bing_page_rows_to_csv, site),
        ),
        Stream(
            label=f"{site.name} {Provider.SITEMAP.value}",
            fetch=partial(fetch_sitemap_data, sitemap_url=site.sitemap_url),
            csv_path=partial(sitemap_csv_path, site),
            write_rows=partial(write_sitemap_rows_to_csv, site),
        ),
        Stream(
            label=f"{site.name} {Provider.CRAWL.value} pages",
            fetch=partial(fetch_crawl_data, site_url=site.bing_site_url),
            csv_path=partial(crawl_csv_path, site),
            write_rows=partial(write_crawl_rows_to_csv, site),
        ),
        Stream(
            label=f"{site.name} {Provider.IMAGES.value}",
            fetch=partial(fetch_crawl_data, site_url=site.bing_site_url),
            csv_path=partial(image_csv_path, site),
            write_rows=partial(write_image_rows_to_csv, site),
            response_field="image_rows",
        ),
        Stream(
            label=f"{site.name} Web Core Vitals",
            fetch=partial(fetch_web_core_vitals_data, site_url=site.bing_site_url),
            csv_path=partial(web_core_vitals_csv_path, site),
            write_rows=partial(write_web_core_vitals_rows_to_csv, site),
        ),
        *[ga4_stream(site, report) for report in GA4_REPORTS],
    )


def is_due(site: Site, today: date) -> bool:
    return site.schedule is not Schedule.QUARTERLY or today.month in QUARTERLY_MONTHS


def ensure_month_data(
    stream: Stream,
    today: date,
    months_back: int,
    *,
    overwrite: bool,
) -> tuple[str, int]:
    """Collect one stream for one month.

    Returns ``(status, row_count)`` where ``status`` is ``"stored"`` for a
    freshly fetched month or ``"skipped"`` when an existing CSV was kept.
    Failures raise ``OSError``/``RuntimeError`` and are handled by the caller.
    """
    month_start, month_end = month_range(today, months_back)
    csv_path = stream.csv_path(today, months_back)

    if csv_path.exists() and not overwrite:
        logger.info(
            "Skipping %s (%s): already present at %s",
            month_start.strftime("%Y-%m"),
            stream.label,
            csv_path,
        )
        return "skipped", 0

    response = stream.fetch(start_date=month_start, end_date=month_end)
    rows = response.get(stream.response_field) or []
    stream.write_rows(rows, today, months_back)
    logger.info(
        "Stored %d %s rows for %s in %s",
        len(rows),
        stream.label,
        month_start.strftime("%Y-%m"),
        csv_path,
    )
    return "stored", len(rows)


@dataclass
class StreamResult:
    label: str
    month: str
    status: str  # "stored" | "skipped" | "failed"
    row_count: int = 0
    error: str | None = None


@dataclass
class SiteResult:
    name: str
    results: list[StreamResult] = field(default_factory=list)
    failures: int = 0


@dataclass
class AuditRunResult:
    started_at: str
    finished_at: str
    duration_seconds: float
    sites: list[SiteResult] = field(default_factory=list)
    failures: int = 0


def run_site_collect(site: Site, today: date) -> SiteResult:
    results: list[StreamResult] = []
    failures = 0
    for months_back in range(1, site.history_months + 1):
        overwrite = months_back <= site.always_fetch_months
        month_label = month_range(today, months_back)[0].strftime("%Y-%m")
        for stream in streams_for(site):
            try:
                status, row_count = ensure_month_data(
                    stream, today, months_back, overwrite=overwrite
                )
            except (OSError, RuntimeError) as error:
                failures += 1
                logger.error(
                    "Failed to collect %s for %s: %s",
                    stream.label,
                    month_label,
                    error,
                )
                results.append(
                    StreamResult(stream.label, month_label, "failed", 0, str(error))
                )
            else:
                results.append(
                    StreamResult(stream.label, month_label, status, row_count)
                )
    return SiteResult(site.name, results, failures)


def run_audit(
    *,
    site_names: list[str] | None = None,
    today: date | None = None,
) -> AuditRunResult:
    """Run the audit for every scheduled site (or a subset) and report results.

    The crawl stream caches one snapshot per site *per process*; the cache is
    cleared here so a long-lived API process still re-crawls on every run.
    """
    clear_crawl_cache()
    validate_credentials()
    validate_bing_credentials()
    validate_ga4_credentials(SITES)

    anchor = today or datetime.now(UTC).date()
    started_at = datetime.now(UTC)
    site_results: list[SiteResult] = []

    for site in SITES:
        if site_names is not None and site.name not in site_names:
            continue
        if not is_due(site, anchor):
            logger.info(
                "Skipping %s: quarterly audit runs in %s",
                site.name,
                ", ".join(str(month) for month in sorted(QUARTERLY_MONTHS)),
            )
            continue
        site_results.append(run_site_collect(site, anchor))

    finished_at = datetime.now(UTC)
    return AuditRunResult(
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_seconds=(finished_at - started_at).total_seconds(),
        sites=site_results,
        failures=sum(site.failures for site in site_results),
    )
