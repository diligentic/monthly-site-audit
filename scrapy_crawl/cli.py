"""Command line entry point: ``python -m scrapy_crawl``.

This is the process that :mod:`scrapy_crawl.runner` starts, and it is also the
quickest way to check a crawl by hand before it is wired into the monthly
audit::

    python -m scrapy_crawl --site https://diligentic.ca --output /tmp/crawl.jsonl

It reads the same environment variables as the audit service
(``constants/crawl.py``), writes a JSONL feed, and exits non-zero when the crawl
failed. Nothing is uploaded and nothing is kept: the feed is the whole output.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from scrapy.crawler import CrawlerProcess

from scrapy_crawl.errors import CrawlError
from scrapy_crawl.records import read_feed, unusable_crawl_reason
from scrapy_crawl.settings import build_crawl_settings, build_feed_settings
from scrapy_crawl.spiders.seo_audit import SeoAuditSpider

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scrapy_crawl",
        description=(
            "Crawl one site with Scrapy and write a JSONL feed of its internal "
            "pages, external link checks, and sitemap URLs."
        ),
    )
    parser.add_argument(
        "--site",
        required=True,
        help="The site to crawl, for example https://diligentic.ca",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Where to write the JSONL feed.",
    )
    parser.add_argument(
        "--sitemap",
        default=None,
        help=(
            "The XML sitemap to read for the 'In Sitemap' column; defaults to "
            "/sitemap.xml on the site's host. Set CRAWL_SITEMAP=0 to skip sitemaps."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Scrapy log level; defaults to CRAWL_LOG_LEVEL (INFO).",
    )
    return parser


def run_crawl(
    *,
    site_url: str,
    output_file: Path,
    sitemap_url: str | None = None,
    log_level: str | None = None,
) -> None:
    """Run the crawl in this process, streaming records to ``output_file``.

    Raises:
        CrawlError: if the spider could not be started.
    """
    settings = build_crawl_settings()
    if log_level:
        settings["LOG_LEVEL"] = log_level
    settings.update(build_feed_settings(output_file))
    # ``LOG_INSTALL_ROOT_HANDLER`` is what decides who logs to the console. It
    # is left at Scrapy's default: a second handler on the root logger would
    # print every record twice and, being unfiltered, would leak Scrapy's
    # internal DEBUG lines past the configured level into a pipe the parent
    # process has to buffer in full.
    process = CrawlerProcess(settings)
    try:
        process.crawl(
            SeoAuditSpider,
            site_url=site_url,
            sitemap_url=sitemap_url or None,
        )
    except Exception as error:  # noqa: BLE001 - reported as a crawl failure
        raise CrawlError(f"Could not start the crawl of {site_url}: {error}") from error
    # Blocks until the crawl is finished, then stops the reactor.
    process.start(stop_after_crawl=True)


def summarise(output_file: Path, site_url: str) -> str:
    """Return a short, human-readable summary of a finished crawl.

    The feed is the deliverable, so the summary exists to answer the one
    question a person has after a crawl: did it cover the site, and what did it
    find?
    """
    feed = read_feed(output_file)
    indexable = sum(1 for page in feed.pages if page.verdict().indexability == "Indexable")
    broken_external = sum(
        1 for status in feed.external_statuses.values() if status == 0 or status >= 400
    )
    sitemap_note = (
        "unknown (no readable sitemap)"
        if feed.sitemap_urls is None
        else f"{len(feed.sitemap_urls)} URL(s)"
    )
    lines = [
        f"Site:            {site_url}",
        f"Feed:            {output_file}",
        f"Pages crawled:   {len(feed.pages)}",
        f"Indexable:       {indexable}",
        f"External links:  {len(feed.external_statuses)} checked, {broken_external} broken",
        f"Sitemap:         {sitemap_note}",
    ]
    if feed.malformed:
        lines.append(f"Skipped lines:   {feed.malformed} (malformed feed records)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a crawl from the command line. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    output_file: Path = args.output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_crawl(
            site_url=args.site,
            output_file=output_file,
            sitemap_url=args.sitemap,
            log_level=args.log_level,
        )
    except CrawlError as error:
        # Reported on stderr by logging's last-resort handler, which needs no
        # root handler of its own -- see ``run_crawl``.
        logger.error("%s", error)
        return 1
    # The exit code follows the same rule the audit service applies, so a crawl
    # the CLI calls a success is one the service can actually build a report
    # from: a crawl that reached nothing is a failed crawl, not an empty site.
    reason = unusable_crawl_reason(read_feed(output_file))
    if reason:
        logger.error("The crawl of %s is unusable: %s", args.site, reason)
        return 1
    print(summarise(output_file, args.site))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
