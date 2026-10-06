"""The SEO audit spider.

Crawls one site, follows only internal links, and streams one JSONL record per
page to the feed. It is deliberately explicit about the things a crawler
usually hides:

* **Redirects are not followed.** ``REDIRECT_ENABLED`` is off, so a 3xx
  response is reported as the page it was requested on -- which is what the
  report needs -- and an internal ``Location`` is requested by the spider
  itself, as a separate record with its own status.
* **4xx and 5xx responses are pages.** ``HTTPERROR_ALLOW_ALL`` is on, so they
  are recorded with their real status code and their content is still scanned
  for links, because a 404 page can link to a page that is broken too.
* **Normalisation happens before a request is scheduled.** Links are resolved
  and normalised here, and the fingerprint is built from the normalised URL, so
  ``/about`` and ``/about/`` produce one request and one row.
* **Nothing accumulates.** Each page is reduced to a small record, yielded, and
  forgotten. The only state that survives a page is the set of external URLs
  already queued and the sitemap URLs seen, both of which are bounded by
  explicit limits.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from scrapy import Request, Spider
from scrapy.exceptions import CloseSpider, DontCloseSpider, IgnoreRequest
from scrapy.http import HtmlResponse, Response, TextResponse
from scrapy.selector import Selector
from twisted.python.failure import Failure

from constants.crawl import CRAWL_MAX_URLS_ENV_VAR
from constants.sitemap import (
    SITEMAP_MAX_CHILDREN,
    SITEMAP_MAX_INDEX_DEPTH,
)
from scrapy_crawl.indexability import REDIRECT_STATUSES, is_html
from scrapy_crawl.records import ExternalRecord, PageRecord, SitemapRecord
from scrapy_crawl.settings import (
    CRAWL_MAX_PAGES_SETTING,
    excluded_url_params,
    external_link_limits,
    read_sitemap_enabled,
)
from scrapy_crawl.sitemap import SitemapError, is_index, locations, parse_sitemap_xml
from scrapy_crawl.urls import (
    clean_start_url,
    internal_domains_for,
    is_internal,
    normalize_url,
    resolve_url,
)

logger = logging.getLogger(__name__)

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"

#: ``name`` matching is case-insensitive in HTML, so ``Description`` and
#: ``ROBOTS`` have to match the selector too.
_NAME_IS = f'translate(@name, "{_UPPER}", "{_LOWER}")'

_TITLE_XPATH = "//title/text()"
_META_DESCRIPTION_XPATH = f'//meta[{_NAME_IS}="description"]/@content'
_ROBOTS_XPATHS = (
    f'//meta[{_NAME_IS}="robots"]/@content',
    f'//meta[{_NAME_IS}="googlebot"]/@content',
)
_CANONICAL_XPATH = f'//link[translate(@rel, "{_UPPER}", "{_LOWER}")="canonical"]/@href'
_H1_XPATH = "//h1"
#: Body text without the parts that are not prose. Keeps the word count honest
#: and avoids paying for inline scripts on a large page.
_BODY_TEXT_XPATH = (
    "//body//text()[not(ancestor::script) and not(ancestor::style) "
    "and not(ancestor::noscript) and not(ancestor::template)]"
)

#: A sitemap larger than this is not a sitemap any crawler should read; the cap
#: only exists so a hostile or broken file cannot exhaust the container.
_MAX_SITEMAP_URLS = 50_000
#: Same idea for a single response body, in bytes.
_MAX_SITEMAP_BYTES = 10 * 1024 * 1024

#: Statuses that mean "this server will not answer a HEAD request", not "this
#: link is broken". The check falls back to GET for exactly these.
_HEAD_UNSUPPORTED_STATUSES = frozenset({403, 405, 501})
_MAX_EXTERNAL_REDIRECTS = 10


def _collapse(value: str) -> str:
    """Collapse runs of whitespace, including the newlines in served HTML."""
    return " ".join(value.split())


def _header_text(response: Response, name: str) -> str:
    """Return all values of a response header as one comma-separated string."""
    values = response.headers.getlist(name)
    if not values:
        return ""
    parts = [
        _collapse(value.decode("utf-8", errors="replace")) for value in values
    ]
    return ", ".join(part for part in parts if part)


def _element_text(element: Selector) -> str:
    """Return the collapsed text of an element, which may be empty.

    Text nodes are joined with a space so that ``Hello<b>World</b>`` reads as
    ``Hello World`` rather than ``HelloWorld``. An element with no text at all
    returns an empty string and is still counted, because ``<h1></h1>`` is a
    real heading.
    """
    if element is None:
        return ""
    return _collapse(" ".join(element.xpath(".//text()").getall()))


def _is_noindex(*values: str) -> bool:
    """Return whether any robots directive in ``values`` blocks indexing."""
    for value in values:
        for directive in value.split(","):
            token = directive.strip().lower()
            # ``none`` is the obsolete spelling of ``noindex`` and is still
            # honoured by every search engine, so it counts.
            if token in {"noindex", "none"}:
                return True
    return False


def _describe_failure(failure: Failure) -> str:
    """Turn a Twisted failure into a short, readable message.

    ``<Failure scrapy.exceptions.TimeoutError: The response took too long>``
    becomes ``TimeoutError: The response took too long``. The value is
    preferred over the traceback because only the value names the problem.
    """
    value = getattr(failure, "value", None)
    if value is None:
        exception = failure.type
        name = getattr(exception, "__name__", str(exception))
        return _truncate(name)
    name = type(value).__name__
    message = _collapse(str(value))
    return _truncate(f"{name}: {message}" if message else name)


def _truncate(value: str, limit: int = 300) -> str:
    """Shorten a message so one dead URL cannot fill a cell in the report."""
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


def _is_ignored(failure: Failure) -> bool:
    """Return whether a request was dropped before it was ever sent.

    Scrapy signals "do not fetch this" with ``IgnoreRequest`` from robots.txt,
    the offsite filter, the duplicate filter, and the response size limit, and
    signals control flow with ``CloseSpider``/``DontCloseSpider``. None of them
    are failures of the site, so none of them may become a report row.
    """
    value = getattr(failure, "value", None)
    if isinstance(value, (IgnoreRequest, CloseSpider, DontCloseSpider)):
        return True
    return failure.check(CloseSpider) is not None


def _first(values: Iterable[str]) -> str:
    """Return the first non-empty string, collapsed."""
    for value in values:
        cleaned = _collapse(value)
        if cleaned:
            return cleaned
    return ""


def _is_html_response(response: Response) -> bool:
    """Return whether the response body can be parsed as HTML.

    A response served with the wrong content type is still worth parsing when it
    is text: a page sent as ``text/plain`` still contributes links. A binary
    body is not, and parsing one would waste the container's memory.
    """
    if isinstance(response, HtmlResponse):
        return True
    if not isinstance(response, TextResponse):
        return False
    return is_html(_header_text(response, "Content-Type"))


def _html_selector(response: Response) -> Selector:
    """Return an HTML selector for any text response.

    ``TextResponse.selector`` would be correct for a response served as HTML and
    for one served as XML, but not for a page mislabelled as ``text/plain``.
    Building the selector explicitly keeps element extraction HTML-shaped in
    every case.
    """
    if isinstance(response, HtmlResponse):
        return response.selector
    return Selector(text=response.text, type="html")


def _url_has_excluded_param(url: str, excluded: frozenset[str]) -> bool:
    """Return whether the URL carries a tracking parameter that is dropped."""
    if not excluded:
        return False
    return any(name.lower() in excluded for name, _ in parse_qsl(urlsplit(url).query))


class SeoAuditSpider(Spider):
    """Crawls one site and streams one record per internal page.

    Args:
        site_url: the URL the crawl starts from, usually the site root.
        sitemap_url: the XML sitemap to read for the "In Sitemap" column.
            Defaults to ``/sitemap.xml`` on the start URL's host.
    """

    name = "seo_audit"

    def __init__(
        self,
        site_url: str | None = None,
        sitemap_url: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not site_url and not self.start_urls:
            raise ValueError(
                "SeoAuditSpider requires a 'site_url' argument or start_urls."
            )
        raw_start = site_url or self.start_urls[0]
        self.site_url = clean_start_url(raw_start)
        self.internal_domains = internal_domains_for(self.site_url)
        if not self.internal_domains:
            raise ValueError(f"{self.site_url!r} has no usable host name.")
        # Only the registrable domain is listed, so ``www`` and the apex host
        # (and any subdomain) are all treated as internal.
        self.allowed_domains = sorted(self.internal_domains)

        parts = urlsplit(self.site_url)
        self.sitemap_url = (
            resolve_url(sitemap_url) or f"{parts.scheme}://{parts.netloc}/sitemap.xml"
        )
        self.read_sitemap = read_sitemap_enabled()

        # Overwritten from the crawler settings in ``from_crawler``; the values
        # here keep the spider usable on its own, and a setting that is absent
        # (Scrapy's default is 0, meaning "no limit") leaves them in place.
        self.max_pages = 600
        self.external_link_limit = 200
        self.external_host_limit = 100
        self.excluded_params = excluded_url_params()

        # Counters and bounded state. Everything else is derived from the
        # response in hand and dropped with it.
        self.pages_crawled = 0
        self.pages_failed = 0
        self.pages_non_200 = 0
        self.redirects = 0
        self.external_checked: set[str] = set()
        self.external_hosts: set[str] = set()
        self.external_skipped = 0
        self.sitemaps_read = 0
        self.sitemap_url_count = 0
        self._seen_internal: set[str] = set()
        self._warning_budget = False
        self._warning_limits = False
        #: How many internal pages have been *requested*. The budget is spent
        # here, not on responses received -- see ``_page_request``.
        self.pages_scheduled = 0

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "SeoAuditSpider":
        """Build the spider and read its budgets from the crawler settings."""
        spider = super().from_crawler(crawler, *args, **kwargs)
        settings = crawler.settings
        # The page budget, not ``CLOSESPIDER_PAGECOUNT``: that one counts
        # responses, including the sitemap and the external link checks, and is
        # set above the page budget on purpose (see
        # :func:`scrapy_crawl.settings.non_page_response_headroom`).
        page_budget = settings.getint(CRAWL_MAX_PAGES_SETTING)
        if page_budget > 0:
            spider.max_pages = page_budget
        link_limit, host_limit = external_link_limits()
        spider.external_link_limit = link_limit
        spider.external_host_limit = host_limit
        spider.excluded_params = excluded_url_params()
        return spider

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    async def start(self) -> AsyncIterator[Any]:
        """Start the crawl with the site URL and the sitemap.

        This is an async generator rather than a plain one: Scrapy calls
        ``start()`` on the Twisted reactor, so it may only ``yield`` its
        requests one at a time.
        """
        first = self._page_request(self.site_url, depth=0)
        if first is not None:
            yield first
        if self.read_sitemap:
            for request in self._sitemap_request(self.sitemap_url, depth=0):
                yield request

    def _page_request(self, url: str, *, depth: int) -> Request | None:
        """Return a request for one internal page, or ``None`` past the budget.

        The budget is enforced here, where the request is created, rather than
        when the response comes back. Scrapy keeps several requests in flight
        and queues every link a page offers, so a check made on the way *back*
        from a page only discovers the overrun after the whole frontier has
        been scheduled -- the crawl would then carry on past its budget and
        report more pages than it was allowed to fetch. Counting requests as
        they are made bounds the crawl at the number it was given.
        """
        if self.pages_scheduled >= self.max_pages:
            self._warn_budget()
            return None
        self.pages_scheduled += 1
        return Request(
            url,
            callback=self.parse_page,
            errback=self.handle_page_failure,
            meta={"page_depth": depth},
            # Duplicate filtering is wanted here: a site that links to its own
            # home page from every page must not re-fetch it.
            dont_filter=False,
        )

    def _sitemap_request(self, url: str, *, depth: int) -> Iterator[Request]:
        """Return the request for one sitemap, or nothing if it is not wanted.

        A sitemap is published for crawlers, and robots.txt regularly blocks
        paths like ``/sitemap.xml`` while leaving the site open, so the crawl's
        own rules do not apply to it. ``allow_offsite`` is the same idea taken to
        its conclusion: a sitemap index is an explicit instruction from the site
        to read the files it points at, and those files are often hosted
        elsewhere (a CDN, or a staging host).
        """
        if depth > SITEMAP_MAX_INDEX_DEPTH:
            logger.warning(
                "Not reading the sitemap at %s: it is nested deeper than %d level(s).",
                url,
                SITEMAP_MAX_INDEX_DEPTH,
            )
            return
        yield Request(
            url,
            callback=self.parse_sitemap,
            errback=self.handle_sitemap_failure,
            meta={
                "sitemap_depth": depth,
                "dont_obey_robotstxt": True,
                "allow_offsite": True,
            },
        )

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    def parse_page(self, response: Response) -> Iterator[Any]:
        """Record one page and schedule its links, redirect target, and outlinks."""
        self.pages_crawled += 1
        address = response.url
        depth = int(response.meta.get("page_depth", 0))

        if response.status in REDIRECT_STATUSES:
            self.redirects += 1
            yield self._redirect_record(response, depth=depth).as_item()
            for request in self._redirect_requests(response, depth=depth):
                yield request
            return

        if response.status != 200:
            self.pages_non_200 += 1

        title = meta_description = canonical = ""
        h1s: tuple[str, ...] = ()
        noindex = False
        word_count = 0
        internal_links: tuple[str, ...] = ()
        external_links: tuple[str, ...] = ()

        if _is_html_response(response):
            selector = _html_selector(response)
            internal_links, external_links = self._split_links(selector, address)
            # Links are read from every HTML response, including a 404 page: a
            # broken page can link to a page that is broken too. The content
            # elements are only read on a successful one, which is exactly where
            # ``_has_content`` in the report layer looks for them.
            if 200 <= response.status < 300:
                title = _first(selector.xpath(_TITLE_XPATH).getall())
                meta_description = _first(
                    selector.xpath(_META_DESCRIPTION_XPATH).getall()
                )
                canonical = self._canonical(selector, address)
                h1s = tuple(
                    _element_text(node) for node in selector.xpath(_H1_XPATH)
                )
                noindex = _is_noindex(
                    *(
                        value
                        for xpath in _ROBOTS_XPATHS
                        for value in selector.xpath(xpath).getall()
                    ),
                    _header_text(response, "X-Robots-Tag"),
                )
                word_count = len(
                    " ".join(selector.xpath(_BODY_TEXT_XPATH).getall()).split()
                )

        record = PageRecord(
            address=address,
            status=response.status,
            content_type=_header_text(response, "Content-Type"),
            depth=depth,
            title=title,
            meta_description=meta_description,
            h1s=h1s,
            canonical=canonical,
            noindex=noindex,
            word_count=word_count,
            internal_links=internal_links,
            external_links=external_links,
        ).with_verdict()
        yield record.as_item()

        for request in self._page_requests(internal_links, depth=depth):
            yield request
        for request in self._external_requests(external_links):
            yield request

    def handle_page_failure(self, failure: Failure) -> Iterator[Any]:
        """Record a page that could not be fetched at all, as status 0.

        A DNS failure, a refused connection, or a timeout leaves no response to
        inspect. The URL still has to appear in the report: an internal link
        that cannot be fetched is a broken link, and reporting it as status 0
        is what makes it visible. ``Indexability Status`` then reads
        ``Non-200 (0)``, with the underlying error in ``Indexability Reason``.
        """
        request = failure.request
        if request is None:  # pragma: no cover - Scrapy always sets the request
            return
        if _is_ignored(failure):
            # The request never left the crawler: robots.txt, an offsite rule,
            # a duplicate, or a size limit. Recording it as status 0 would
            # report a page that nobody was allowed to fetch as a broken link.
            # It stays an inlink of the page that referenced it, which is how
            # the report lists it as "not crawled".
            logger.debug("Not crawled %s: %s", request.url, _describe_failure(failure))
            return
        error = _describe_failure(failure)
        if self._is_page_request(request):
            self.pages_failed += 1
            self.pages_crawled += 1
            depth = int(request.meta.get("page_depth", 0))
            logger.warning("Could not fetch %s: %s", request.url, error)
            record = PageRecord(
                address=request.url,
                status=0,
                depth=depth,
                error=error,
            ).with_verdict()
            yield record.as_item()
        else:
            # An external check that failed is reported in its own record type,
            # so the page report stays about pages.
            url = request.meta.get("external_url") or request.url
            yield ExternalRecord(
                url=url, status=0, method=request.method, error=error
            ).as_item()

    def _is_page_request(self, request: Request) -> bool:
        """Return whether a request was scheduled as an internal page."""
        meta = request.meta
        return not meta.get("sitemap_depth") is not None and not meta.get(
            "external_url"
        )

    # ------------------------------------------------------------------
    # Redirects
    # ------------------------------------------------------------------
    def _redirect_record(self, response: Response, *, depth: int) -> PageRecord:
        return PageRecord(
            address=response.url,
            status=response.status,
            depth=depth,
            redirect_to=self._redirect_target(response),
        ).with_verdict()

    @staticmethod
    def _redirect_target(response: Response) -> str:
        """Return the absolute destination of a redirect response."""
        location = _header_text(response, "Location")
        if not location:
            return ""
        return resolve_url(location, response.url)

    def _redirect_requests(
        self, response: Response, *, depth: int
    ) -> Iterator[Request]:
        """Follow an internal redirect target; record an external one and stop.

        Following the hop is what turns ``/old-page`` into a row *and* the page
        it points at into a row. Leaving an external redirect alone is
        deliberate: it is a fact to report, not somewhere to crawl.
        """
        target = self._redirect_target(response)
        if not target:
            logger.warning("Redirect without a usable Location header: %s", response.url)
            return
        if not is_internal(target, self.internal_domains):
            return
        request = self._page_request(target, depth=depth + 1)
        if request is not None:
            yield request

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------
    def _split_links(
        self, selector: Selector, address: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split the links on a page into internal and external URLs.

        Every href is resolved against the page and normalised, so the reported
        links and the requested URLs are the same strings, and self-links are
        dropped: a page linking to itself has no inlink to contribute.
        """
        internal: list[str] = []
        external: list[str] = []
        seen_internal: set[str] = set()
        seen_external: set[str] = set()
        own_key = normalize_url(address)

        for link in selector.xpath("//a[@href] | //area[@href]"):
            url = resolve_url(link.xpath("@href").get() or "", address)
            if not url:
                continue
            key = normalize_url(url)
            if not key:
                continue
            if is_internal(url, self.internal_domains):
                if key == own_key or key in seen_internal:
                    continue
                if _url_has_excluded_param(url, self.excluded_params):
                    continue
                seen_internal.add(key)
                internal.append(url)
            else:
                if key in seen_external:
                    continue
                seen_external.add(key)
                external.append(url)
        return tuple(internal), tuple(external)

    def _page_requests(
        self, links: tuple[str, ...], *, depth: int
    ) -> Iterator[Request]:
        """Schedule one request per new internal link, up to the page budget.

        Links are taken in the order the page lists them, so the crawl follows
        the site's own priority order and stops at the budget on the first
        links of the page that crosses it -- the rest of that page's links are
        not fetched, and the pages that link to them appear in ``issues.csv``
        as "Internal link not crawled".
        """
        for url in links:
            key = normalize_url(url)
            if key in self._seen_internal:
                continue
            self._seen_internal.add(key)
            request = self._page_request(url, depth=depth + 1)
            if request is None:
                return
            yield request

    @staticmethod
    def _canonical(selector: Selector, address: str) -> str:
        """Return the absolute canonical URL of a page, or an empty string."""
        return resolve_url(_first(selector.xpath(_CANONICAL_XPATH).getall()), address)

    # ------------------------------------------------------------------
    # External link checks
    # ------------------------------------------------------------------
    def _external_requests(self, links: tuple[str, ...]) -> Iterator[Request]:
        """Queue a HEAD request for each distinct external link.

        Links are verified rather than trusted, because an audit that reports
        only internal status codes cannot tell a reader whether the "read more"
        links still work. Two limits keep this bounded: a maximum number of
        distinct URLs, and a maximum number of distinct hosts (one robots.txt
        per host is cached in memory). When a limit is hit the remaining links
        are left unchecked, and that is logged rather than silently dropped.
        """
        for url in links:
            key = normalize_url(url)
            if not key or key in self.external_checked:
                continue
            host = urlsplit(url).hostname or ""
            if len(self.external_checked) >= self.external_link_limit:
                self.external_skipped += 1
                continue
            if host not in self.external_hosts and (
                len(self.external_hosts) >= self.external_host_limit
            ):
                self.external_skipped += 1
                continue
            self.external_checked.add(key)
            if host:
                self.external_hosts.add(host)
            yield Request(
                url,
                method="HEAD",
                callback=self.parse_external,
                errback=self.handle_external_failure,
                # ``allow_offsite`` lets the request past the offsite filter; it
                # is still checked against the host's own robots.txt, which is
                # the polite thing to do with a stranger's server.
                meta={"external_url": url, "allow_offsite": True},
                dont_filter=False,
            )

    def parse_external(self, response: Response) -> Iterator[Any]:
        """Record the result of one external check, retrying HEAD with GET.

        A 405, 403, or 501 means the server does not answer HEAD, not that the
        link is broken -- plenty of servers still refuse it. Those three codes
        are retried once with GET before anything is called broken.
        """
        original_url = response.meta.get("external_url") or response.url
        if (
            response.request.method == "HEAD"
            and response.status in _HEAD_UNSUPPORTED_STATUSES
        ):
            yield Request(
                original_url,
                method="GET",
                callback=self.parse_external,
                errback=self.handle_external_failure,
                meta={
                    "external_url": original_url,
                    "allow_offsite": True,
                    "retried_from_head": response.status,
                },
                dont_filter=True,
            )
            return
        if response.status in REDIRECT_STATUSES:
            location = response.headers.get(b"Location", b"").decode(
                "utf-8", errors="replace"
            ).strip()
            target = resolve_url(location, response.url) if location else ""
            visited = set(response.meta.get("external_redirects", ()))
            visited.add(normalize_url(response.url))
            target_key = normalize_url(target)
            if (
                target
                and target_key not in visited
                and len(visited) < _MAX_EXTERNAL_REDIRECTS
            ):
                yield Request(
                    target,
                    method="GET" if response.status == 303 else response.request.method,
                    callback=self.parse_external,
                    errback=self.handle_external_failure,
                    meta={
                        "external_url": original_url,
                        "allow_offsite": True,
                        "external_redirects": tuple(visited),
                    },
                    dont_filter=True,
                )
                return
        yield ExternalRecord(
            url=original_url, status=response.status, method=response.request.method
        ).as_item()

    def handle_external_failure(self, failure: Failure) -> Iterator[Any]:
        """Record an external link that could not be checked, as status 0.

        A request that never left the crawler is not a broken link. Most often
        that is the external host's own ``robots.txt`` refusing the check, and
        reporting a stranger's crawl policy as "the link is dead" would be
        wrong, so the link is simply left unchecked.
        """
        request = failure.request
        if request is None:  # pragma: no cover - Scrapy always sets the request
            return
        if _is_ignored(failure):
            logger.debug(
                "Not checking the external link %s: %s",
                request.meta.get("external_url") or request.url,
                _describe_failure(failure),
            )
            return
        url = request.meta.get("external_url") or request.url
        yield ExternalRecord(
            url=url, status=0, method=request.method, error=_describe_failure(failure)
        ).as_item()

    # ------------------------------------------------------------------
    # Sitemaps
    # ------------------------------------------------------------------
    def parse_sitemap(self, response: Response) -> Iterator[Any]:
        """Record a sitemap's URLs, and follow a sitemap index one level down.

        Only the URLs matter: they are normalised and kept so the report can say
        whether a page is in the sitemap. A sitemap index is recorded as an
        index and its children are requested, because a large site splits its
        sitemap into per-section files.
        """
        if response.status != 200:
            logger.warning(
                "Sitemap %s returned HTTP %s; the 'In Sitemap' column will be unknown.",
                response.url,
                response.status,
            )
            return
        if len(response.body) > _MAX_SITEMAP_BYTES:
            logger.warning(
                "Sitemap %s is larger than %d bytes and was not read.",
                response.url,
                _MAX_SITEMAP_BYTES,
            )
            return
        try:
            root = parse_sitemap_xml(response.body, source=response.url)
        except SitemapError as error:
            logger.warning(
                "Sitemap %s could not be read (%s); the 'In Sitemap' column will be "
                "unknown.",
                response.url,
                error,
            )
            return

        depth = int(response.meta.get("sitemap_depth", 0))
        index = is_index(root)
        resolved = [
            url
            for url in (
                resolve_url(loc, response.url)
                for loc in locations(
                    root, limit=_MAX_SITEMAP_URLS, source=response.url
                )
            )
            if url
        ]
        self.sitemaps_read += 1
        if not index:
            self.sitemap_url_count += len(resolved)
        yield SitemapRecord(
            url=response.url, urls=tuple(resolved), is_index=index
        ).as_item()

        if not index:
            return
        for url in resolved[:SITEMAP_MAX_CHILDREN]:
            yield from self._sitemap_request(url, depth=depth + 1)
        if len(resolved) > SITEMAP_MAX_CHILDREN:
            logger.warning(
                "The sitemap index at %s lists %d sitemap files; only the first %d "
                "were read.",
                response.url,
                len(resolved),
                SITEMAP_MAX_CHILDREN,
            )

    def handle_sitemap_failure(self, failure: Failure) -> Iterator[Any]:
        """Note that a sitemap could not be read, without failing the crawl."""
        request = failure.request
        url = request.url if request is not None else self.sitemap_url
        if _is_ignored(failure):
            # The request never left the crawler (an offsite rule, a duplicate,
            # the size limit). That is not a sitemap that could not be read.
            logger.debug(
                "Not reading the sitemap at %s: %s", url, _describe_failure(failure)
            )
            return
        logger.warning(
            "Could not read the sitemap at %s: %s",
            url,
            _describe_failure(failure),
        )

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------
    def _warn_budget(self) -> None:
        """Warn once that the page budget stopped the crawl.

        The decision itself is made in :meth:`_page_request`, where the request
        is created; this only says so, once, naming the setting to raise. It is
        worth a warning because a crawl that stops at its budget looks exactly
        like a complete audit of a small site, and the difference matters to
        whoever reads the CSVs.
        """
        if self._warning_budget:
            return
        self._warning_budget = True
        logger.warning(
            "Reached the crawl budget of %d page(s); no further internal pages "
            "will be requested. Raise %s to crawl more of the site.",
            self.max_pages,
            CRAWL_MAX_URLS_ENV_VAR,
        )

    def _warn_limits(self) -> None:
        if self._warning_limits or not self.external_skipped:
            return
        self._warning_limits = True
        logger.warning(
            "Left %d external link(s) unchecked (limits: %d URL(s), %d host(s)).",
            self.external_skipped,
            self.external_link_limit,
            self.external_host_limit,
        )

    def closed(self, reason: str) -> None:
        """Log what the crawl actually did; the report is built from the feed."""
        self._warn_limits()
        logger.info(
            "Crawl finished site=%s reason=%s pages=%d/%d failed=%d non_200=%d "
            "redirects=%d external_checked=%d sitemaps=%d sitemap_urls=%d",
            self.site_url,
            reason,
            self.pages_crawled,
            self.max_pages,
            self.pages_failed,
            self.pages_non_200,
            self.redirects,
            len(self.external_checked),
            self.sitemaps_read,
            self.sitemap_url_count,
        )
        if self.pages_crawled == 0:
            logger.error(
                "The crawl of %s returned no page. Check that the site is reachable "
                "and that robots.txt allows %s.",
                self.site_url,
                self.allowed_domains[0] if self.allowed_domains else "the crawler",
            )
