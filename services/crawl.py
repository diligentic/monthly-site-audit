"""Crawl a site with Scrapy and expose one audit stream per CSV report.

One crawl per site, run by :mod:`scrapy_crawl`, produces a JSONL feed: one
record per internal page, one per checked external link, and one per XML
sitemap that was read. That feed is read line by line, reduced to small
:class:`~utils.crawl_report.CrawlPage` records in memory, and then serialised
into the five required reports (``internal``, ``h1``, ``meta_description``,
``page_titles`` and ``issues``). The feed is never written to persistent disk:
it lives in a private temporary directory that is removed as soon as the upload
to Google Drive has completed.

The crawl itself runs in a child process (see :mod:`scrapy_crawl.runner`), so
Scrapy never runs inside the web service. Scrapy's reactor is a process-wide,
single-use object: crawling in-process would install a Twisted reactor beside
Uvicorn's event loop and the first crawl would take the web server down with it.
A child process also means the crawler's memory is returned to the container the
moment it ends, and that a crash in the crawler is an exit code rather than a
dead worker. This module does import Scrapy -- once, through
:func:`~scrapy_crawl.runner.require_scrapy`, so that a missing dependency is a
clear error rather than an empty feed -- but importing it installs no reactor
and starts nothing.

All reports share a per-site cache, so a site is crawled once per audit run
even when several months are missing from Google Drive. The cache is cleared
before every run by :func:`clear_crawl_cache`.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from constants.crawl import (
    CRAWL_EXPORTS,
    CRAWL_ROBOTS_TXT_ENV_VAR,
    META_DESCRIPTION_MAX_LENGTH_DEFAULT,
    META_DESCRIPTION_MAX_LENGTH_ENV_VAR,
    META_DESCRIPTION_MIN_LENGTH_DEFAULT,
    META_DESCRIPTION_MIN_LENGTH_ENV_VAR,
    TITLE_MAX_LENGTH_DEFAULT,
    TITLE_MAX_LENGTH_ENV_VAR,
    TITLE_MIN_LENGTH_DEFAULT,
    TITLE_MIN_LENGTH_ENV_VAR,
)
from scrapy_crawl.errors import CrawlError
from scrapy_crawl.records import PageRecord, read_feed, unusable_crawl_reason
from scrapy_crawl.runner import run_crawl as run_scrapy_crawl
from scrapy_crawl.settings import env_int
from scrapy_crawl.urls import clean_start_url, normalize_url
from utils.crawl_report import CrawlPage, CrawlReport, CrawlThresholds

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()

#: Suffix of the JSONL feed inside the crawl's temporary directory.
_FEED_NAME = "crawl.jsonl"


@dataclass
class _CachedCrawl:
    report: CrawlReport
    error: BaseException | None = None
    cleanup: Callable[[], None] | None = None


_CRAWL_CACHE: dict[tuple[str, str], _CachedCrawl] = {}


def clear_crawl_cache() -> None:
    """Forget cached crawl reports so the next audit re-crawls the sites."""
    with _CACHE_LOCK:
        _CRAWL_CACHE.clear()


# --------------------------------------------------------------------------
# Environment configuration
# --------------------------------------------------------------------------
def build_thresholds() -> CrawlThresholds:
    """Build the on-page SEO issue thresholds from the environment."""
    return CrawlThresholds(
        title_min_length=env_int(
            TITLE_MIN_LENGTH_ENV_VAR, TITLE_MIN_LENGTH_DEFAULT, minimum=0
        ),
        title_max_length=env_int(
            TITLE_MAX_LENGTH_ENV_VAR, TITLE_MAX_LENGTH_DEFAULT, minimum=1
        ),
        meta_description_min_length=env_int(
            META_DESCRIPTION_MIN_LENGTH_ENV_VAR,
            META_DESCRIPTION_MIN_LENGTH_DEFAULT,
            minimum=0,
        ),
        meta_description_max_length=env_int(
            META_DESCRIPTION_MAX_LENGTH_ENV_VAR,
            META_DESCRIPTION_MAX_LENGTH_DEFAULT,
            minimum=1,
        ),
    )


# --------------------------------------------------------------------------
# Feed parsing
# --------------------------------------------------------------------------
def _to_page(
    record: PageRecord, sitemap_urls: frozenset[str] | None
) -> CrawlPage:
    """Reduce one feed record to the page it describes."""
    return CrawlPage(
        url=record.address,
        status=record.status,
        crawl_error=record.error,
        depth=record.depth,
        title=record.title,
        meta_description=record.meta_description,
        # The feed keeps empty H1 elements as empty strings, so ``<h1></h1>``
        # stays a real H1 and the H1 count matches the page.
        h1s=list(record.h1s),
        canonical=record.canonical,
        noindex=record.noindex,
        content_type=record.content_type,
        redirect_to=record.redirect_to,
        word_count=record.word_count,
        internal_links=list(record.internal_links),
        external_links=list(record.external_links),
        in_sitemap=(
            None if sitemap_urls is None else normalize_url(record.address) in sitemap_urls
        ),
    )


def read_pages(
    output_file: Path, site_url: str
) -> tuple[list[CrawlPage], dict[str, int], bool]:
    """Read a JSONL crawl feed into pages and the external link statuses.

    Returns:
        The pages of the crawl, the normalised external URL -> HTTP status
        mapping the crawl produced, and whether a sitemap could be read at all
        (which is what separates "not in any sitemap" from "unknown").

    Records are parsed and reduced one at a time, so the feed is never held in
    memory as a whole. Duplicate documents (for example ``/a`` and ``/a/``,
    which the spider's fingerprinter already collapses) keep the first record.

    Raises:
        CrawlError: if the feed could not produce a report, per the shared rule
            in :func:`scrapy_crawl.records.unusable_crawl_reason`.
    """
    feed = read_feed(output_file)
    reason = unusable_crawl_reason(feed)
    if reason:
        raise CrawlError(
            f"The crawl of {site_url} is unusable: {reason}. Check that the site is "
            f"reachable and that robots.txt allows the crawler. "
            f"{CRAWL_ROBOTS_TXT_ENV_VAR}=0 only helps for a site that asks not to "
            "be crawled, such as a local test."
        )

    pages: list[CrawlPage] = []
    seen: set[str] = set()
    for record in feed.pages:
        key = normalize_url(record.address)
        if not record.address or key in seen:
            logger.debug("Skipping duplicate crawl record for %s", record.address)
            continue
        seen.add(key)
        pages.append(_to_page(record, feed.sitemap_urls))

    if feed.sitemap_urls is None:
        logger.info(
            "No XML sitemap could be read; the 'In Sitemap' column will be blank."
        )
    else:
        logger.info(
            "Read %d URL(s) from %d sitemap file(s)",
            len(feed.sitemap_urls),
            sum(1 for sitemap in feed.sitemaps if not sitemap.is_index),
        )
    logger.info(
        "Parsed %d unique internal URL(s) and %d checked external link(s) "
        "from the crawl feed",
        len(pages),
        len(feed.external_statuses),
    )
    return pages, feed.external_statuses, feed.sitemap_urls is not None


# --------------------------------------------------------------------------
# Crawl execution
# --------------------------------------------------------------------------
def _crawl_and_cache(site_url: str, sitemap_url: str | None) -> _CachedCrawl:
    """Crawl one site, build its report, and cache it for the audit run."""
    start_url = clean_start_url(site_url)
    tmpdir = tempfile.TemporaryDirectory(prefix="scrapy-crawl-")
    output_file = Path(tmpdir.name) / _FEED_NAME
    logger.info("Crawl working directory: %s", output_file.parent)
    try:
        run_scrapy_crawl(start_url, output_file, sitemap_url=sitemap_url)
        pages, external_statuses, sitemap_known = read_pages(output_file, start_url)
        report = CrawlReport(
            pages,
            thresholds=build_thresholds(),
            external_statuses=external_statuses,
        )
    except Exception as error:  # noqa: BLE001 - cached so we never re-crawl
        tmpdir.cleanup()
        with _CACHE_LOCK:
            _CRAWL_CACHE[(site_url, sitemap_url or "")] = _CachedCrawl(
                report=CrawlReport([]), error=error
            )
        raise

    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        tmpdir.cleanup()
        logger.info("Removed crawl working directory: %s", output_file.parent)

    with _CACHE_LOCK:
        _CRAWL_CACHE[(site_url, sitemap_url or "")] = _CachedCrawl(
            report=report, cleanup=cleanup
        )
    logger.info(
        "Crawl report ready site=%s pages=%d sitemap=%s",
        site_url,
        report.page_count,
        "read" if sitemap_known else "unavailable",
    )
    return _CRAWL_CACHE[(site_url, sitemap_url or "")]


def _report_for(site_url: str, sitemap_url: str | None) -> _CachedCrawl:
    key = (site_url, sitemap_url or "")
    with _CACHE_LOCK:
        cached = _CRAWL_CACHE.get(key)
    if cached is not None:
        if cached.error is not None:
            raise cached.error
        return cached
    return _crawl_and_cache(site_url, sitemap_url)


def fetch_crawl_export(
    *,
    export: str,
    site_url: str,
    sitemap_url: str | None = None,
    start_date: Any = None,
    end_date: Any = None,
) -> dict[str, Any]:
    """Return one crawl report as serialisable rows.

    Args:
        export: which of the five crawl reports to build.
        site_url: the site to crawl.
        sitemap_url: the XML sitemap that fills the "In Sitemap" column. When it
            is ``None`` the crawler falls back to ``/sitemap.xml`` on the site's
            own host, and reports the column as unknown if that cannot be read.
        start_date: and ``end_date``: accepted so this function matches the
            audit stream interface; a crawl is a point-in-time snapshot of the
            site, not a range.

    The result also carries a ``cleanup`` hook that removes the crawl working
    directory once the caller has uploaded the CSV to Google Drive.
    """
    if export not in CRAWL_EXPORTS:
        choices = ", ".join(sorted(CRAWL_EXPORTS))
        raise ValueError(f"Unknown crawl export {export!r}; choose one of {choices}.")
    del start_date, end_date
    cached = _report_for(site_url, sitemap_url)
    return {"rows": cached.report.rows(export), "cleanup": cached.cleanup}
