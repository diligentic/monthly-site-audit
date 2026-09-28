"""Turn a set of crawled pages into the five crawl report row sets.

The report is the single place where the crawl is interpreted:

* it normalises and orders the pages so every report is deterministic;
* it maps each normalised URL to its status, which is what makes broken-link
  and non-200 detection possible for URLs that were never crawled;
* it counts internal and external links, and drops the links a page makes to
  itself;
* it derives indexability and the indexability reason;
* it reports one row per URL and issue in ``issues.csv``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from constants.crawl import (
    CRAWL_EXPORTS,
    META_DESCRIPTION_MAX_LENGTH_DEFAULT,
    META_DESCRIPTION_MIN_LENGTH_DEFAULT,
    TITLE_MAX_LENGTH_DEFAULT,
    TITLE_MIN_LENGTH_DEFAULT,
)

#: Every report is about HTML documents; PDFs, images, and other binary assets
#: are still listed in ``internal.csv`` but never get content-element issues.
_HTML_CONTENT_TYPE = re.compile(r"html|xml", re.IGNORECASE)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

INDEXABLE = "Indexable"
NON_INDEXABLE = "Non-Indexable"

SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

_MAX_LISTED_URLS = 3


def normalize_url(url: str) -> str:
    """Normalise a URL for status lookups and duplicate detection.

    The fragment is dropped because it never identifies a different document,
    and a trailing slash is only kept for the site root so ``/about`` and
    ``/about/`` resolve to the same page.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


@dataclass
class CrawlPage:
    """One crawled URL, reduced to the fields the reports need."""

    url: str
    status: int | None = None
    crawl_error: str = ""
    depth: int | None = None
    title: str = ""
    meta_description: str = ""
    h1s: list[str] = field(default_factory=list)
    canonical: str = ""
    noindex: bool = False
    content_type: str = ""
    redirect_to: str = ""
    word_count: int = 0
    internal_links: list[str] = field(default_factory=list)
    internal_link_texts: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Normalised URL used to look this page up in the status map."""
        return normalize_url(self.url)


@dataclass(frozen=True)
class CrawlThresholds:
    """Length boundaries used by the on-page SEO issues."""

    title_min_length: int = TITLE_MIN_LENGTH_DEFAULT
    title_max_length: int = TITLE_MAX_LENGTH_DEFAULT
    meta_description_min_length: int = META_DESCRIPTION_MIN_LENGTH_DEFAULT
    meta_description_max_length: int = META_DESCRIPTION_MAX_LENGTH_DEFAULT


def _is_html(page: CrawlPage) -> bool:
    """Return whether a page is an HTML document.

    A missing content type is treated as HTML: most sites declare
    ``<meta charset>`` instead of an ``http-equiv`` content type, so advertools
    has nothing to report for them.
    """
    if not page.content_type:
        return True
    return bool(_HTML_CONTENT_TYPE.search(page.content_type))


def _has_content(page: CrawlPage) -> bool:
    """Return whether content elements should be checked for a page.

    Titles, H1s, and meta descriptions are only meaningful on a successfully
    served HTML document; error pages are reported through their status code.
    """
    return page.status is not None and 200 <= page.status < 300 and _is_html(page)


def _format_urls(urls: Iterable[str]) -> str:
    ordered = sorted(set(urls))
    listed = ordered[:_MAX_LISTED_URLS]
    remaining = len(ordered) - len(listed)
    joined = ", ".join(listed)
    return f"{joined} and {remaining} more" if remaining > 0 else joined


@dataclass(frozen=True)
class _Issue:
    url: str
    issue: str
    category: str
    severity: str
    details: str

    def as_row(self) -> dict[str, Any]:
        return {
            "URL": self.url,
            "Issue": self.issue,
            "Category": self.category,
            "Severity": self.severity,
            "Details": self.details,
        }


class CrawlReport:
    """The interpreted result of one crawl of one site."""

    def __init__(
        self,
        pages: Sequence[CrawlPage],
        *,
        thresholds: CrawlThresholds | None = None,
    ) -> None:
        self.thresholds = thresholds or CrawlThresholds()
        self.pages: list[CrawlPage] = sorted(pages, key=lambda page: page.key)
        self._by_key: dict[str, CrawlPage] = {page.key: page for page in self.pages}
        self._inlinks: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._collect_inlinks()
        self._details: dict[str, dict[str, Any]] = {
            page.key: self._describe(page) for page in self.pages
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def page_count(self) -> int:
        """Number of unique internal URLs that were crawled."""
        return len(self.pages)

    def rows(self, export: str) -> list[dict[str, Any]]:
        """Return the rows of one crawl report."""
        if export not in CRAWL_EXPORTS:
            choices = ", ".join(sorted(CRAWL_EXPORTS))
            raise ValueError(
                f"Unknown crawl export {export!r}; choose one of {choices}."
            )
        return {
            "internal": self.internal_rows,
            "h1": self.h1_rows,
            "meta_description": self.meta_description_rows,
            "page_titles": self.page_title_rows,
            "issues": self.issue_rows,
        }[export]()

    # ------------------------------------------------------------------
    # Derived crawl data
    # ------------------------------------------------------------------
    def _collect_inlinks(self) -> None:
        """Record, for every URL, the pages that link to it.

        A redirect counts as a link to its destination: a page that links to an
        old URL sends the visitor to the destination, so the destination is not
        an orphan.
        """
        for page in self.pages:
            seen: set[str] = set()
            for link, text in zip(page.internal_links, page.internal_link_texts):
                key = normalize_url(link)
                if key in seen or key == page.key:
                    continue
                seen.add(key)
                self._inlinks[key].append((page.url, text))
            if page.redirect_to:
                target = normalize_url(page.redirect_to)
                if target != page.key and target not in seen:
                    self._inlinks[target].append((page.url, ""))

    def _indexability(self, page: CrawlPage) -> tuple[str, str, str]:
        """Return ``(indexability, status, reason)`` for one page."""
        if page.crawl_error or page.status is None:
            reason = page.crawl_error or "No response status was returned"
            return NON_INDEXABLE, "Crawl Error", reason
        status = page.status
        if status >= 500:
            return NON_INDEXABLE, "Server Error (5xx)", f"HTTP {status}"
        if status >= 400:
            return NON_INDEXABLE, "Client Error (4xx)", f"HTTP {status}"
        if status in _REDIRECT_STATUSES:
            target = f" -> {page.redirect_to}" if page.redirect_to else ""
            return NON_INDEXABLE, "Redirect", f"HTTP {status}{target}"
        if status < 200:
            return NON_INDEXABLE, "Informational (1xx)", f"HTTP {status}"
        if page.noindex:
            return (
                NON_INDEXABLE,
                "Blocked By Meta X-Robots-Tag",
                "noindex / none robots directive",
            )
        if page.canonical and normalize_url(page.canonical) != page.key:
            return NON_INDEXABLE, "Canonical", f"Canonical -> {page.canonical}"
        return INDEXABLE, INDEXABLE, ""

    def _describe(self, page: CrawlPage) -> dict[str, Any]:
        """Build the per-page data shared by the reports and the issues."""
        indexability, indexability_status, reason = self._indexability(page)
        inlinks = self._inlinks.get(page.key, [])
        internal_link_count = sum(1 for _ in page.internal_links)
        return {
            "page": page,
            "indexability": indexability,
            "indexability_status": indexability_status,
            "indexability_reason": reason,
            "inlinks": len(inlinks),
            "unique_inlinks": len({url for url, _ in inlinks}),
            "outlinks": internal_link_count,
            "unique_outlinks": len({normalize_url(link) for link in page.internal_links}),
            "external_outlinks": len(page.external_links),
        }

    # ------------------------------------------------------------------
    # Report rows
    # ------------------------------------------------------------------
    def internal_rows(self) -> list[dict[str, Any]]:
        """One row per crawled URL, with its indexability and link counts."""
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            details = self._details[page.key]
            rows.append(
                {
                    "URL": page.url,
                    "Status Code": page.status if page.status is not None else 0,
                    "Indexability": details["indexability"],
                    "Indexability Status": details["indexability_status"],
                    "Indexability Reason": details["indexability_reason"],
                    "Canonical URL": page.canonical,
                    "Inlinks": details["inlinks"],
                    "Unique Inlinks": details["unique_inlinks"],
                    "Outlinks": details["outlinks"],
                    "Unique Outlinks": details["unique_outlinks"],
                    "External Outlinks": details["external_outlinks"],
                    "Word Count": page.word_count,
                    "Content Type": page.content_type,
                    "Crawl Depth": page.depth if page.depth is not None else "",
                }
            )
        return rows

    def h1_rows(self) -> list[dict[str, Any]]:
        """One row per H1; a page without an H1 gets a single empty row.

        An empty ``<h1></h1>`` is a real element, so it keeps its own row and is
        counted; a page with no H1 at all reports a count of zero.
        """
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            h1s = page.h1s if _has_content(page) else []
            if not h1s:
                rows.append(
                    {"URL": page.url, "H1": "", "H1 count": 0, "H1 length": 0}
                )
                continue
            for h1 in h1s:
                rows.append(
                    {
                        "URL": page.url,
                        "H1": h1,
                        "H1 count": len(h1s),
                        "H1 length": len(h1),
                    }
                )
        return rows

    def meta_description_rows(self) -> list[dict[str, Any]]:
        """One row per URL with its meta description and length.

        A page whose content elements were not read (an error page, a PDF, ...)
        keeps an empty description and is not flagged as missing one, so the CSV
        and ``issues.csv`` always agree.
        """
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            checked = _has_content(page)
            description = page.meta_description if checked else ""
            rows.append(
                {
                    "URL": page.url,
                    "Meta Description": description,
                    "Meta Description length": len(description),
                    "Missing Meta Description": checked and not description,
                }
            )
        return rows

    def page_title_rows(self) -> list[dict[str, Any]]:
        """One row per URL with its page title and length.

        A page whose content elements were not read keeps an empty title and is
        not flagged as missing one, for the same reason as
        :meth:`meta_description_rows`.
        """
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            checked = _has_content(page)
            title = page.title if checked else ""
            rows.append(
                {
                    "URL": page.url,
                    "Page Title": title,
                    "Page Title length": len(title),
                    "Missing Page Title": checked and not title,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------
    def issue_rows(self) -> list[dict[str, Any]]:
        """One row per URL and detected issue, sorted by URL and severity."""
        issues: list[_Issue] = []
        issues.extend(self._content_issues())
        issues.extend(self._duplicate_issues())
        issues.extend(self._status_issues())
        issues.extend(self._link_issues())
        issues.extend(self._canonical_issues())
        issues.sort(
            key=lambda issue: (
                issue.url,
                SEVERITY_RANK.get(issue.severity, len(SEVERITY_RANK)),
                issue.category,
                issue.issue,
                issue.details,
            )
        )
        return [issue.as_row() for issue in issues]

    def _content_issues(self) -> list[_Issue]:
        """Missing, too short, and too long content elements."""
        thresholds = self.thresholds
        issues: list[_Issue] = []
        for page in self.pages:
            if not _has_content(page):
                continue
            issues.extend(self._title_issues(page, thresholds))
            issues.extend(self._meta_description_issues(page, thresholds))
            issues.extend(self._h1_issues(page))
            if not page.canonical:
                issues.append(
                    _Issue(
                        page.url,
                        "Missing canonical",
                        "Canonicals",
                        "Low",
                        "The page has no rel=canonical link element",
                    )
                )
        return issues

    def _title_issues(self, page: CrawlPage, thresholds: CrawlThresholds) -> list[_Issue]:
        length = len(page.title)
        if not page.title:
            return [
                _Issue(
                    page.url,
                    "Missing title",
                    "Page titles",
                    "High",
                    "The page has no title element",
                )
            ]
        issues: list[_Issue] = []
        if length < thresholds.title_min_length:
            issues.append(
                _Issue(
                    page.url,
                    "Title too short",
                    "Page titles",
                    "Medium",
                    f"{length} characters, minimum is "
                    f"{thresholds.title_min_length}",
                )
            )
        elif length > thresholds.title_max_length:
            issues.append(
                _Issue(
                    page.url,
                    "Title too long",
                    "Page titles",
                    "Low",
                    f"{length} characters, maximum is "
                    f"{thresholds.title_max_length}",
                )
            )
        return issues

    def _meta_description_issues(
        self, page: CrawlPage, thresholds: CrawlThresholds
    ) -> list[_Issue]:
        if not page.meta_description:
            return [
                _Issue(
                    page.url,
                    "Missing meta description",
                    "Meta descriptions",
                    "High",
                    "The page has no meta description",
                )
            ]
        length = len(page.meta_description)
        issues: list[_Issue] = []
        if length < thresholds.meta_description_min_length:
            issues.append(
                _Issue(
                    page.url,
                    "Meta description too short",
                    "Meta descriptions",
                    "Medium",
                    f"{length} characters, minimum is "
                    f"{thresholds.meta_description_min_length}",
                )
            )
        elif length > thresholds.meta_description_max_length:
            issues.append(
                _Issue(
                    page.url,
                    "Meta description too long",
                    "Meta descriptions",
                    "Low",
                    f"{length} characters, maximum is "
                    f"{thresholds.meta_description_max_length}",
                )
            )
        return issues

    def _h1_issues(self, page: CrawlPage) -> list[_Issue]:
        if not any(page.h1s):
            # ``h1s`` keeps empty elements, so a page whose only H1 is
            # ``<h1></h1>`` is treated as having no H1 text.
            return [
                _Issue(
                    page.url,
                    "Missing H1",
                    "Headings",
                    "High",
                    "The page has no H1 element"
                    if not page.h1s
                    else "The page has no H1 text",
                )
            ]
        if len(page.h1s) > 1:
            return [
                _Issue(
                    page.url,
                    "Multiple H1s",
                    "Headings",
                    "Medium",
                    f"{len(page.h1s)} H1 elements on one page",
                )
            ]
        return []

    def _duplicate_issues(self) -> list[_Issue]:
        """Duplicate titles, meta descriptions, and H1s, one row per URL."""
        issues: list[_Issue] = []
        for value_of, issue, category, severity in (
            (lambda page: page.title, "Duplicate title", "Page titles", "High"),
            (
                lambda page: page.meta_description,
                "Duplicate meta description",
                "Meta descriptions",
                "Medium",
            ),
            (lambda page: page.h1s[0] if page.h1s else "", "Duplicate H1", "Headings", "Medium"),
        ):
            values: dict[str, list[str]] = defaultdict(list)
            for page in self.pages:
                if not _has_content(page):
                    continue
                value = value_of(page).strip()
                if value:
                    values[value].append(page.url)
            for value, urls in values.items():
                if len(urls) < 2:
                    continue
                for url in urls:
                    others = _format_urls(other for other in urls if other != url)
                    issues.append(
                        _Issue(
                            url,
                            issue,
                            category,
                            severity,
                            f'"{value}" is also used by {others}',
                        )
                    )
        return issues

    def _status_issues(self) -> list[_Issue]:
        """One row per internal URL that is not a 200."""
        issues: list[_Issue] = []
        for page in self.pages:
            if page.status is None:
                issues.append(
                    _Issue(
                        page.url,
                        "Non-200 internal URL",
                        "Status codes",
                        "High",
                        f"Crawl error: {page.crawl_error or 'no response'}",
                    )
                )
                continue
            if page.status == 200:
                continue
            # A redirect is not an error, but it is still a non-200 internal URL.
            severity = "High" if page.status >= 400 else "Low"
            target = f" -> {page.redirect_to}" if page.redirect_to else ""
            issues.append(
                _Issue(
                    page.url,
                    "Non-200 internal URL",
                    "Status codes",
                    severity,
                    f"HTTP {page.status}{target}",
                )
            )
        return issues

    def _link_issues(self) -> list[_Issue]:
        """Broken, redirecting, and unverifiable internal links."""
        issues: list[_Issue] = []
        for page in self.pages:
            broken: dict[str, str] = {}
            redirecting: dict[str, str] = {}
            unverified: list[str] = []
            for link in dict.fromkeys(page.internal_links):
                target = self._by_key.get(normalize_url(link))
                if target is None:
                    unverified.append(link)
                elif target.crawl_error or target.status is None:
                    broken[link] = (
                        f"crawl error ({target.crawl_error or 'no response'})"
                    )
                elif target.status >= 400:
                    broken[link] = f"HTTP {target.status}"
                elif target.status in _REDIRECT_STATUSES:
                    where = f" -> {target.redirect_to}" if target.redirect_to else ""
                    redirecting[link] = f"HTTP {target.status}{where}"
            for link, detail in sorted(broken.items()):
                issues.append(
                    _Issue(
                        page.url,
                        "Broken internal link",
                        "Internal links",
                        "High",
                        f"{link} ({detail})",
                    )
                )
            for link, detail in sorted(redirecting.items()):
                issues.append(
                    _Issue(
                        page.url,
                        "Internal link to a redirect",
                        "Internal links",
                        "Low",
                        f"{link} ({detail})",
                    )
                )
            if unverified:
                issues.append(
                    _Issue(
                        page.url,
                        "Internal link not crawled",
                        "Internal links",
                        "Low",
                        f"{len(unverified)} link(s) outside the crawl budget: "
                        f"{_format_urls(unverified)}",
                    )
                )
        return issues

    def _canonical_issues(self) -> list[_Issue]:
        """Canonicals that point somewhere invalid or outside the site."""
        issues: list[_Issue] = []
        for page in self.pages:
            if not page.canonical:
                continue
            target = self._by_key.get(normalize_url(page.canonical))
            if target is None:
                # Not detectable: the canonical was never crawled, so its status
                # is unknown. Blocking canonical targets are also not crawled.
                continue
            if target.crawl_error or target.status is None:
                issues.append(
                    _Issue(
                        page.url,
                        "Canonical points to a non-200 URL",
                        "Canonicals",
                        "High",
                        f"{page.canonical} (crawl error: "
                        f"{target.crawl_error or 'no response'})",
                    )
                )
            elif target.status >= 400:
                issues.append(
                    _Issue(
                        page.url,
                        "Canonical points to a non-200 URL",
                        "Canonicals",
                        "High",
                        f"{page.canonical} (HTTP {target.status})",
                    )
                )
        return issues
