import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from typing import Any

from constants.crawl import SCREAMING_FROG_EXPORTS
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
from services.drive import (
    GoogleDriveError,
    GoogleDriveStorage,
    validate_drive_credentials,
)
from services.ga4 import fetch_ga4_data, validate_ga4_credentials
from services.screaming_frog import (
    clear_screaming_frog_cache,
    fetch_screaming_frog_export,
)
from services.search_console import (
    fetch_page_data,
    fetch_query_data,
    validate_credentials,
)
from services.sitemap import fetch_sitemap_data
from services.web_core_vitals import fetch_web_core_vitals_data
from utils.bing_csv import (
    bing_pages_csv_name,
    bing_queries_csv_name,
    serialize_bing_page_rows,
    serialize_bing_query_rows,
)
from utils.crawl_csv import (
    legacy_screaming_frog_csv_name,
    raw_csv_bytes,
    screaming_frog_csv_name,
)
from utils.dates import month_range
from utils.ga4_csv import ga4_csv_name, serialize_ga4_rows
from utils.pages_csv import pages_csv_name, serialize_page_rows
from utils.queries_csv import queries_csv_name, serialize_query_rows
from utils.sitemap_csv import serialize_sitemap_rows, sitemap_csv_name
from utils.web_core_vitals_csv import (
    serialize_web_core_vitals_rows,
    web_core_vitals_csv_name,
)
from utils.get_env import ConfigurationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stream:
    label: str
    fetch: Callable[..., dict[str, Any]]
    drive_path: Callable[..., str]
    serialize: Callable[..., bytes]
    response_field: str = "rows"
    legacy_drive_path: Callable[..., str] | None = None


def _drive_relative_path(
    site: Site,
    provider: Provider,
    csv_name: Callable[..., str],
    today: date,
    months_back: int,
) -> str:
    """Relative Drive path such as ``Diligentic/GSC/queries_2026-09.csv``."""
    return f"{site.name}/{provider.value}/{csv_name(today, months_back)}"


def _month_drive_relative_path(
    site: Site,
    provider: Provider,
    csv_name: Callable[..., str],
    today: date,
    months_back: int,
) -> str:
    """Build a Drive path with a ``YYYY-MM`` directory."""
    month = month_range(today, months_back)[0]
    return (
        f"{site.name}/{provider.value}/{month:%Y-%m}/"
        f"{csv_name(today, months_back)}"
    )


def _make_stream(
    *,
    site: Site,
    provider: Provider,
    label: str,
    fetch: Callable[..., dict[str, Any]],
    csv_name: Callable[..., str],
    serialize: Callable[..., bytes],
    response_field: str = "rows",
) -> Stream:
    return Stream(
        label=label,
        fetch=fetch,
        drive_path=partial(_drive_relative_path, site, provider, csv_name),
        serialize=serialize,
        response_field=response_field,
    )


def ga4_stream(site: Site, report: GA4Report) -> Stream:
    return _make_stream(
        site=site,
        provider=Provider.GA4,
        label=f"{site.name} {Provider.GA4.value} {report.label}",
        fetch=partial(
            fetch_ga4_data,
            property_id_env_var=site.ga4_property_id_env_var,
            report=report,
        ),
        csv_name=partial(ga4_csv_name, report),
        serialize=partial(serialize_ga4_rows, report=report),
    )


def screaming_frog_streams(site: Site) -> tuple[Stream, ...]:
    """Return all five tab exports plus the Issues Overview for one site."""
    return tuple(
        Stream(
            label=(
                f"{site.name} {Provider.CRAWL.value} Screaming Frog {report_name}"
            ),
            fetch=partial(
                fetch_screaming_frog_export,
                export=export,
                site_url=site.bing_site_url,
            ),
            drive_path=partial(
                _month_drive_relative_path,
                site,
                Provider.CRAWL,
                partial(screaming_frog_csv_name, export),
            ),
            serialize=raw_csv_bytes,
            legacy_drive_path=partial(
                _drive_relative_path,
                site,
                Provider.CRAWL,
                partial(legacy_screaming_frog_csv_name, export),
            ),
        )
        for export, (report_name, _) in SCREAMING_FROG_EXPORTS.items()
    )


def streams_for(site: Site, *, crawl_only: bool = False) -> tuple[Stream, ...]:
    if crawl_only:
        return screaming_frog_streams(site)
    provider = Provider.GSC
    return (
        Stream(
            label=f"{site.name} {provider.value} query",
            fetch=partial(fetch_query_data, site_url=site.gsc_site_url),
            drive_path=partial(_drive_relative_path, site, provider, queries_csv_name),
            serialize=serialize_query_rows,
        ),
        Stream(
            label=f"{site.name} {provider.value} page",
            fetch=partial(fetch_page_data, site_url=site.gsc_site_url),
            drive_path=partial(_drive_relative_path, site, provider, pages_csv_name),
            serialize=serialize_page_rows,
        ),
        Stream(
            label=f"{site.name} {provider.value} Canada query",
            fetch=partial(
                fetch_query_data,
                site_url=site.gsc_site_url,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
            drive_path=partial(
                _drive_relative_path,
                site,
                provider,
                partial(queries_csv_name, country=COUNTRY_FILTER_EXPRESSION),
            ),
            serialize=serialize_query_rows,
        ),
        Stream(
            label=f"{site.name} {provider.value} Canada page",
            fetch=partial(
                fetch_page_data,
                site_url=site.gsc_site_url,
                country=COUNTRY_FILTER_EXPRESSION,
            ),
            drive_path=partial(
                _drive_relative_path,
                site,
                provider,
                partial(pages_csv_name, country=COUNTRY_FILTER_EXPRESSION),
            ),
            serialize=serialize_page_rows,
        ),
        Stream(
            label=f"{site.name} {Provider.BING.value} query",
            fetch=partial(fetch_bing_query_data, site_url=site.bing_site_url),
            drive_path=partial(
                _drive_relative_path, site, Provider.BING, bing_queries_csv_name
            ),
            serialize=serialize_bing_query_rows,
        ),
        Stream(
            label=f"{site.name} {Provider.BING.value} page",
            fetch=partial(fetch_bing_page_data, site_url=site.bing_site_url),
            drive_path=partial(
                _drive_relative_path, site, Provider.BING, bing_pages_csv_name
            ),
            serialize=serialize_bing_page_rows,
        ),
        Stream(
            label=f"{site.name} {Provider.SITEMAP.value}",
            fetch=partial(fetch_sitemap_data, sitemap_url=site.sitemap_url),
            drive_path=partial(
                _drive_relative_path, site, Provider.SITEMAP, sitemap_csv_name
            ),
            serialize=serialize_sitemap_rows,
        ),
        *screaming_frog_streams(site),
        Stream(
            label=f"{site.name} Web Core Vitals",
            fetch=partial(fetch_web_core_vitals_data, site_url=site.bing_site_url),
            drive_path=partial(
                _drive_relative_path,
                site,
                Provider.WEB_CORE_VITALS,
                web_core_vitals_csv_name,
            ),
            serialize=serialize_web_core_vitals_rows,
        ),
        *[ga4_stream(site, report) for report in GA4_REPORTS],
    )


def is_due(site: Site, today: date) -> bool:
    return site.schedule is not Schedule.QUARTERLY or today.month in QUARTERLY_MONTHS


def _build_storage() -> GoogleDriveStorage:
    """Build the Drive client and fail fast when the audit root is not writable.

    If ``audit_data`` cannot be resolved (bad grant, wrong folder id, or a
    Drive outage) every upload would fail individually after expensive
    collection work. Resolving it up front turns that into one clear error.
    """
    storage = GoogleDriveStorage()
    try:
        storage.audit_root_id  # force folder discovery + permission check
    except GoogleDriveError as error:
        raise ConfigurationError(
            f"Google Drive audit root is not writable: {error}"
        ) from error
    return storage


def _migrate_legacy_month_file(
    stream: Stream,
    today: date,
    months_back: int,
    *,
    storage: GoogleDriveStorage,
) -> bool:
    """Move a pre-folder crawl file into its month directory when available."""
    if stream.legacy_drive_path is None:
        return False
    legacy_path = stream.legacy_drive_path(today, months_back)
    if not storage.file_exists(legacy_path):
        return False
    canonical_path = stream.drive_path(today, months_back)
    logger.info(
        "Migrating existing Google Drive file %s to %s without refetching",
        legacy_path,
        canonical_path,
    )
    storage.move_file(legacy_path, canonical_path)
    return True


def ensure_month_data(
    stream: Stream,
    today: date,
    months_back: int,
    *,
    storage: GoogleDriveStorage,
) -> tuple[str, int]:
    """Collect one stream for one month and upload it to Google Drive.

    Returns ``(status, row_count)`` where ``status`` is ``"stored"`` for a
    freshly fetched month or ``"skipped"`` when the CSV already exists on
    Drive and was kept. Legacy flat crawl files are moved into the canonical
    month folder and treated as skipped. Failures raise ``RuntimeError`` and
    are handled by the caller.
    """
    month_start, month_end = month_range(today, months_back)
    relative_path = stream.drive_path(today, months_back)

    if storage.file_exists(relative_path):
        logger.info(
            "Skipping %s (%s): already on Google Drive at %s",
            month_start.strftime("%Y-%m"),
            stream.label,
            relative_path,
        )
        return "skipped", 0

    if _migrate_legacy_month_file(
        stream, today, months_back, storage=storage
    ):
        logger.info(
            "Skipping %s (%s): migrated an existing Drive file to %s",
            month_start.strftime("%Y-%m"),
            stream.label,
            relative_path,
        )
        return "skipped", 0

    response = stream.fetch(start_date=month_start, end_date=month_end)
    rows = response.get(stream.response_field) or []
    try:
        storage.upload_csv(relative_path, stream.serialize(rows))
    finally:
        # Streams that own external resources (e.g. the Screaming Frog
        # temporary crawl folder) expose a cleanup hook on their fetch result
        # so the temporary files are removed after the upload - and never
        # leak when the upload fails.
        cleanup = response.get("cleanup")
        if cleanup is not None:
            cleanup()
    logger.info(
        "Stored %d %s rows for %s on Google Drive as %s",
        len(rows),
        stream.label,
        month_start.strftime("%Y-%m"),
        relative_path,
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


def run_site_collect(
    site: Site,
    today: date,
    storage: GoogleDriveStorage,
    *,
    crawl_only: bool = False,
) -> SiteResult:
    results: list[StreamResult] = []
    failures = 0
    site_streams = streams_for(site, crawl_only=crawl_only)
    for months_back in range(1, site.history_months + 1):
        month_label = month_range(today, months_back)[0].strftime("%Y-%m")
        for stream in site_streams:
            try:
                status, row_count = ensure_month_data(
                    stream,
                    today,
                    months_back,
                    storage=storage,
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
    crawl_only: bool = False,
) -> AuditRunResult:
    """Run the audit for every scheduled site (or a subset) and report results.

    All collected CSVs are uploaded to Google Drive under ``audit_data``; no
    data is written to local disk. Existing Drive files are never fetched or
    overwritten. With ``crawl_only=True``, only the five Screaming Frog tab
    exports and Issues Overview report are collected; non-crawl API credentials
    are not required.

    The crawl cache is cleared for every run so a long-lived API process does
    not reuse a snapshot from an earlier run.
    """
    clear_screaming_frog_cache()
    validate_drive_credentials()
    if not crawl_only:
        validate_credentials()
        validate_bing_credentials()
        validate_ga4_credentials(SITES)
    storage = _build_storage()

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
        site_results.append(
            run_site_collect(site, anchor, storage, crawl_only=crawl_only)
        )

    finished_at = datetime.now(UTC)
    return AuditRunResult(
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_seconds=(finished_at - started_at).total_seconds(),
        sites=site_results,
        failures=sum(site.failures for site in site_results),
    )
