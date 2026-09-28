"""The JSONL feed written by the spider and read by the report layer.

One line is one record, and every line carries a ``type`` discriminator:

``page``
    One internal URL, as it was requested. ``address`` is always the original
    requested URL: redirects are not followed automatically, so a redirect and
    the page it points at are two separate records.
``external``
    The result of checking one external link with ``HEAD`` (or with ``GET``
    after a ``HEAD`` that the server refused).
``sitemap``
    The URLs of one sitemap file. ``is_index`` is ``True`` for a
    ``<sitemapindex>``, whose URLs point at further sitemap files rather than
    at pages.

The dataclasses below are the single definition of the feed: the spider
serialises them with :meth:`PageRecord.as_item`, and the reader rebuilds them
with ``from_dict``. Fields are plain JSON values, so the feed stays readable
with ``jq`` and stays streamable -- a crawl is never held in memory as a whole.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from scrapy_crawl.indexability import (
    INDEXABLE,
    STATUS_INDEXABLE,
    Indexability,
    evaluate_indexability,
)
from scrapy_crawl.urls import normalize_url

logger = logging.getLogger(__name__)

PAGE = "page"
EXTERNAL = "external"
SITEMAP = "sitemap"


def _text(value: Any) -> str:
    """Return ``value`` as a stripped string, or ``""`` for anything else."""
    return value.strip() if isinstance(value, str) else ""


def _count(value: Any) -> int:
    """Return ``value`` as a non-negative int, or ``0`` when unusable."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _flag(value: Any) -> bool:
    return value is True or value == "true"


def _texts(value: Any, *, keep_empty: bool = False) -> tuple[str, ...]:
    """Return a tuple of strings from a JSON list.

    Empty entries are dropped unless ``keep_empty`` is set: the H1 list keeps
    them, because ``<h1></h1>`` is a real element and dropping it would shift
    every following H1 one position away from the page.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        item.strip() if isinstance(item, str) else ""
        for item in value
        if isinstance(item, str) and (keep_empty or item.strip())
    )


@dataclass(frozen=True)
class ExternalRecord:
    """The ``HEAD`` (or retried ``GET``) result for one external link."""

    url: str
    status: int = 0
    method: str = "HEAD"
    error: str = ""

    def as_item(self) -> dict[str, Any]:
        return {"type": EXTERNAL, **asdict(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExternalRecord":
        return cls(
            url=_text(data.get("url")),
            status=_count(data.get("status")),
            method=_text(data.get("method")) or "HEAD",
            error=_text(data.get("error")),
        )


@dataclass(frozen=True)
class SitemapRecord:
    """The ``<loc>`` values of one sitemap file."""

    url: str
    urls: tuple[str, ...] = ()
    is_index: bool = False

    def as_item(self) -> dict[str, Any]:
        return {"type": SITEMAP, **asdict(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SitemapRecord":
        return cls(
            url=_text(data.get("url")),
            urls=_texts(data.get("urls")),
            is_index=_flag(data.get("is_index")),
        )


@dataclass(frozen=True)
class PageRecord:
    """One crawled internal URL with everything the reports need.

    ``internal_links`` and ``external_links`` are the absolute addresses found
    on the page. They are kept in the feed so inlink counts and external link
    checks can be resolved after the crawl, when every page is known: a link to
    a page that is crawled later still counts.
    """

    address: str
    status: int = 0
    content_type: str = ""
    depth: int = 0
    title: str = ""
    meta_description: str = ""
    h1s: tuple[str, ...] = ()
    canonical: str = ""
    redirect_to: str = ""
    noindex: bool = False
    word_count: int = 0
    error: str = ""
    indexability: str = INDEXABLE
    indexability_status: str = STATUS_INDEXABLE
    indexability_reason: str = ""
    internal_links: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()

    def as_item(self) -> dict[str, Any]:
        return {"type": PAGE, **asdict(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PageRecord":
        """Rebuild a record from a feed line, tolerating missing fields."""
        address = _text(data.get("address"))
        return cls(
            address=address,
            status=_count(data.get("status")),
            content_type=_text(data.get("content_type")),
            depth=_count(data.get("depth")),
            title=_text(data.get("title")),
            meta_description=_text(data.get("meta_description")),
            h1s=_texts(data.get("h1s"), keep_empty=True),
            canonical=_text(data.get("canonical")),
            redirect_to=_text(data.get("redirect_to")),
            noindex=_flag(data.get("noindex")),
            word_count=_count(data.get("word_count")),
            error=_text(data.get("error")),
            indexability=_text(data.get("indexability")) or INDEXABLE,
            indexability_status=(
                _text(data.get("indexability_status")) or STATUS_INDEXABLE
            ),
            indexability_reason=_text(data.get("indexability_reason")),
            internal_links=_texts(data.get("internal_links")),
            external_links=_texts(data.get("external_links")),
        )

    def verdict(self) -> Indexability:
        """Re-evaluate the indexability rules for this record.

        The stored verdict is what the spider computed while the page was in
        memory. Recomputing it here from the same fields is how the report layer
        stays independent of the crawl: if the rules in
        :mod:`scrapy_crawl.indexability` change, the CSV follows without
        re-crawling.
        """
        return evaluate_indexability(
            url=self.address,
            status=self.status,
            content_type=self.content_type,
            noindex=self.noindex,
            canonical=self.canonical,
            error=self.error,
        )

    def with_verdict(self) -> "PageRecord":
        """Return a copy of this record with its indexability verdict filled in."""
        verdict = self.verdict()
        return replace(
            self,
            indexability=verdict.indexability,
            indexability_status=verdict.status,
            indexability_reason=verdict.reason,
        )


@dataclass(frozen=True)
class Feed:
    """A parsed crawl feed.

    Attributes:
        pages: the internal page records, in crawl order.
        external: the checked external links, keyed by normalised URL.
        sitemap_urls: the normalised page URLs listed in the XML sitemaps, or
            ``None`` when no sitemap could be read. ``None`` means "unknown",
            which is reported differently from an empty set ("not in any
            sitemap").
        sitemaps: the sitemap files that were read, for logging.
        malformed: the number of feed lines that could not be parsed.
    """

    pages: tuple[PageRecord, ...] = ()
    external: Mapping[str, ExternalRecord] = field(default_factory=dict)
    sitemap_urls: frozenset[str] | None = None
    sitemaps: tuple[SitemapRecord, ...] = ()
    malformed: int = 0

    @property
    def external_statuses(self) -> dict[str, int]:
        """Return normalised external URL -> HTTP status (0 = request failed)."""
        return {url: record.status for url, record in self.external.items()}


def unusable_crawl_reason(feed: Feed) -> str:
    """Return why a finished crawl cannot produce a report, or ``""`` if it can.

    A crawl that reached no URL at all is a failed crawl, not a site with no
    pages: uploading five CSVs built from it would turn a broken crawl into a
    clean-looking audit run. A *partially* failed crawl is fine -- the URLs that
    did not answer are reported with status 0, which is the useful outcome --
    so only a crawl in which nothing answered is unusable.

    This is the single rule both the command line and the audit service apply,
    so a crawl that the CLI calls a success is one the service can report on.
    """
    if not feed.pages:
        return "the crawl found no internal URL"
    if any(page.status for page in feed.pages):
        return ""
    errors = sorted({page.error for page in feed.pages if page.error})
    detail = "; ".join(errors[:3]) or "no response status was returned"
    return f"no response for any of the {len(feed.pages)} URL(s): {detail}"


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield the parsed records of a feed file, one line at a time.

    Lines are decoded as they arrive, so a feed is never held in memory as a
    whole. A line that is not valid JSON, or that is not an object, is counted
    as malformed and skipped: a truncated crawl should still produce reports
    for the pages that were read.
    """
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                yield {}
                continue
            yield record if isinstance(record, dict) else {}


def read_feed(path: Path) -> Feed:
    """Read a crawl feed into a :class:`Feed`.

    Duplicate page records (the same normalised URL reached twice) keep the
    first one, and a sitemap is only "known" once at least one ``<urlset>`` has
    been read -- a sitemap index alone lists sitemap files, not pages.
    """
    pages: list[PageRecord] = []
    seen: set[str] = set()
    external: dict[str, ExternalRecord] = {}
    sitemaps: list[SitemapRecord] = []
    sitemap_urls: set[str] = set()
    sitemap_known = False
    malformed = 0

    for data in iter_records(path):
        record_type = _text(data.get("type"))
        if not record_type:
            malformed += 1
            continue
        if record_type == PAGE:
            record = PageRecord.from_dict(data)
            key = normalize_url(record.address) or record.address
            if not record.address:
                malformed += 1
                continue
            if key in seen:
                logger.debug("Ignoring duplicate feed record for %s", record.address)
                continue
            seen.add(key)
            pages.append(record)
        elif record_type == EXTERNAL:
            record = ExternalRecord.from_dict(data)
            key = normalize_url(record.url) or record.url
            if record.url and key not in external:
                external[key] = record
        elif record_type == SITEMAP:
            record = SitemapRecord.from_dict(data)
            sitemaps.append(record)
            if not record.is_index:
                sitemap_known = True
                for url in record.urls:
                    key = normalize_url(url)
                    if key:
                        sitemap_urls.add(key)
        else:
            malformed += 1

    if malformed:
        logger.warning("Skipped %d malformed crawl feed line(s)", malformed)
    return Feed(
        pages=tuple(pages),
        external=external,
        sitemap_urls=frozenset(sitemap_urls) if sitemap_known else None,
        sitemaps=tuple(sitemaps),
        malformed=malformed,
    )
