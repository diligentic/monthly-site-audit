"""Crawl a site with `advertools` and expose one audit stream per CSV report.

One `advertools.crawl()` run per site produces a jsonlines file with the SEO
elements and links of every internal page. That file is read line by line,
reduced to small :class:`CrawlPage` records in memory, and then serialised into
the five required reports (``internal``, ``h1``, ``meta_description``,
``page_titles`` and ``issues``). The crawl output is therefore never written to
a persistent disk: the jsonlines file lives in a private temporary directory
that is removed as soon as the upload to Google Drive has completed.

All reports share a per-site cache, so a site is crawled once per audit run
even when several months are missing from Google Drive. The cache is cleared
before every run by :func:`clear_crawl_cache`.
"""

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit, urlunsplit

import advertools as adv
import tldextract

from constants.crawl import (
    CRAWL_CONCURRENT_REQUESTS_DEFAULT,
    CRAWL_CONCURRENT_REQUESTS_ENV_VAR,
    CRAWL_DOWNLOAD_DELAY_DEFAULT,
    CRAWL_DOWNLOAD_DELAY_ENV_VAR,
    CRAWL_EXCLUDE_URL_PARAMS_DEFAULT,
    CRAWL_EXCLUDE_URL_PARAMS_ENV_VAR,
    CRAWL_EXPORTS,
    CRAWL_LOG_LEVEL_DEFAULT,
    CRAWL_LOG_LEVEL_ENV_VAR,
    CRAWL_MAX_DEPTH_DEFAULT,
    CRAWL_MAX_DEPTH_ENV_VAR,
    CRAWL_MAX_URLS_DEFAULT,
    CRAWL_MAX_URLS_ENV_VAR,
    CRAWL_REQUEST_TIMEOUT_ENV_VAR,
    CRAWL_REQUEST_TIMEOUT_SECONDS_DEFAULT,
    CRAWL_RESPONSE_SIZE_LIMIT_MB_DEFAULT,
    CRAWL_RESPONSE_SIZE_LIMIT_MB_ENV_VAR,
    CRAWL_RETRY_TIMES_DEFAULT,
    CRAWL_RETRY_TIMES_ENV_VAR,
    CRAWL_ROBOTS_TXT_DEFAULT,
    CRAWL_ROBOTS_TXT_ENV_VAR,
    CRAWL_TIMEOUT_ENV_VAR,
    CRAWL_TIMEOUT_SECONDS_DEFAULT,
    CRAWL_USER_AGENT_DEFAULT,
    CRAWL_USER_AGENT_ENV_VAR,
    META_DESCRIPTION_MAX_LENGTH_DEFAULT,
    META_DESCRIPTION_MAX_LENGTH_ENV_VAR,
    META_DESCRIPTION_MIN_LENGTH_DEFAULT,
    META_DESCRIPTION_MIN_LENGTH_ENV_VAR,
    TITLE_MAX_LENGTH_DEFAULT,
    TITLE_MAX_LENGTH_ENV_VAR,
    TITLE_MIN_LENGTH_DEFAULT,
    TITLE_MIN_LENGTH_ENV_VAR,
)
from utils.crawl_report import CrawlPage, CrawlReport, CrawlThresholds, normalize_url

logger = logging.getLogger(__name__)

_LIST_SEPARATOR = "@@"

#: Public-suffix lookups run against the snapshot bundled with tldextract, so
#: resolving "www.example.com" and "example.com" to one internal domain needs no
#: network access and no on-disk cache.
_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())

#: Extra values that advertools does not extract by default. The robots
#: attribute name is lower-cased first so ``ROBOTS`` and ``robots`` both match.
#: ``h1_count`` is needed because advertools only writes the ``h1`` column when
#: the joined text is non-empty, so a page whose single H1 is ``<h1></h1>`` is
#: otherwise indistinguishable from a page with no H1 at all.
#: Scrapy's LinkExtractor also counts ``<link href>`` as a link, so the
#: ``canonical`` and ``alternate`` hrefs of a page are filtered out of the link
#: lists afterwards instead of being crawled.
_ROBOTS_XPATH = (
    '//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ",'
    '"abcdefghijklmnopqrstuvwxyz")="{name}"]/@content'
)
_XPATH_SELECTORS: dict[str, str] = {
    "meta_robots": _ROBOTS_XPATH.format(name="robots"),
    "meta_googlebot": _ROBOTS_XPATH.format(name="googlebot"),
    "h1_count": "string(count(//h1))",
}

#: Scrapy settings that keep a crawl cheap on a small container.
#: ``HTTPERROR_ALLOW_ALL`` is required to receive a status code (and HTML) for
#: 4xx/5xx responses instead of a bare Scrapy error. ``TELNETCONSOLE_ENABLED``
#: and ``REMOTE_CONTROL_ENABLED`` are disabled because both bind a local port
#: that can execute code, which must not be exposed from a hosted service.
_BASE_CUSTOM_SETTINGS: dict[str, Any] = {
    "ROBOTSTXT_OBEY": True,
    "HTTPERROR_ALLOW_ALL": True,
    "MEMDEBUG_ENABLED": False,
    "TELNETCONSOLE_ENABLED": False,
    "REMOTE_CONTROL_ENABLED": False,
    "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
    "COOKIES_ENABLED": False,
    "AJAXCRAWL_ENABLED": False,
    "LOGSTATS_INTERVAL": 60,
    "FEED_EXPORT_ENCODING": "utf-8",
    "LOG_LEVEL": "INFO",
}

#: Columns read back from the jsonlines file. Everything else is discarded by
#: the spider, which keeps the file and the memory needed to read it small.
#: ``url`` and ``errors`` are always written by advertools.
_CRAWL_COLUMNS: tuple[str, ...] = (
    "status",
    "title",
    "meta_desc",
    "h1",
    "canonical",
    "alt_href",
    "links_url",
    "links_text",
    "redirect_urls",
    "redirect_reasons",
    "depth",
    "body_text",
    *_XPATH_SELECTORS,
)

#: Response headers read from the crawl output. advertools keeps a header under
#: the name the server sent it with, so every casing has to be matched.
_RESPONSE_HEADER_PATTERNS: tuple[str, ...] = (
    r"^resp_headers_Content-Type$",
    r"^resp_headers_X-Robots-Tag$",
)

#: The ``keep_columns`` patterns handed to the spider: everything else is
#: dropped while the crawl runs, which keeps the output file small.
_CRAWL_COLUMN_PATTERNS: tuple[str, ...] = (
    *(f"^{column}$" for column in _CRAWL_COLUMNS),
    *_RESPONSE_HEADER_PATTERNS,
)

_CACHE_LOCK = threading.Lock()

#: advertools stores a failed request as the repr of a Twisted ``Failure``
#: object, which buries the real message behind the full type path of the
#: exception. Only the exception name and its message are worth putting in a log
#: line or in the ``Details`` column of ``issues.csv``.
_TWISTED_FAILURE = re.compile(
    r"^<twisted\.python\.failure\.Failure (?:[\w.]+\.)?(?P<name>\w+)"
    r"(?:: (?P<message>.*))?>$",
    re.DOTALL,
)


class CrawlError(RuntimeError):
    """Raised when a crawl cannot be completed."""


@dataclass
class _CachedCrawl:
    report: CrawlReport
    error: BaseException | None = None
    cleanup: Any = None


_CRAWL_CACHE: dict[str, _CachedCrawl] = {}


def clear_crawl_cache() -> None:
    """Forget cached crawl reports so the next audit re-crawls the sites."""
    with _CACHE_LOCK:
        _CRAWL_CACHE.clear()


# --------------------------------------------------------------------------
# Environment configuration
# --------------------------------------------------------------------------
def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise CrawlError(f"{name} must be an integer, got {raw!r}") from error
    if value < minimum:
        raise CrawlError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise CrawlError(f"{name} must be a number, got {raw!r}") from error
    if value < minimum:
        raise CrawlError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise CrawlError(f"{name} must be a boolean (true/false), got {raw!r}")


def _list_env(name: str, default: tuple[str, ...]) -> list[str]:
    raw = _env(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _require_scrapy_cli() -> None:
    """Fail with an actionable message when the Scrapy CLI is not installed."""
    if shutil.which("scrapy") is None:
        raise CrawlError(
            "The 'scrapy' command required by advertools.crawl() is not on PATH. "
            "Install the project dependencies (uv sync) and make sure the virtual "
            "environment's bin directory is on PATH."
        )


@contextmanager
def _scrapy_on_path() -> Iterator[None]:
    """Make the Scrapy CLI of the running environment available to advertools.

    ``advertools.crawl()`` starts ``scrapy runspider`` as a subprocess, so the
    console script has to be resolvable through ``PATH``. ``uv run`` and the
    deployed service already put the virtual environment on ``PATH``; this
    covers the case where the application is started with a bare interpreter
    path (``.venv/bin/python main.py``).
    """
    if shutil.which("scrapy") is not None:
        yield
        return
    candidate = Path(sys.executable).parent / "scrapy"
    if not candidate.exists():
        _require_scrapy_cli()
        yield
        return
    previous = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{candidate.parent}{os.pathsep}{previous}"
    try:
        yield
    finally:
        if previous:
            os.environ["PATH"] = previous
        else:
            os.environ.pop("PATH", None)


def build_crawl_settings() -> dict[str, Any]:
    """Build the Scrapy settings for one crawl from the environment."""
    size_limit_mb = _float_env(
        CRAWL_RESPONSE_SIZE_LIMIT_MB_ENV_VAR, CRAWL_RESPONSE_SIZE_LIMIT_MB_DEFAULT
    )
    return {
        **_BASE_CUSTOM_SETTINGS,
        "USER_AGENT": _env(CRAWL_USER_AGENT_ENV_VAR) or CRAWL_USER_AGENT_DEFAULT,
        "ROBOTSTXT_OBEY": _bool_env(
            CRAWL_ROBOTS_TXT_ENV_VAR, CRAWL_ROBOTS_TXT_DEFAULT
        ),
        "LOG_LEVEL": _env(CRAWL_LOG_LEVEL_ENV_VAR) or CRAWL_LOG_LEVEL_DEFAULT,
        "CONCURRENT_REQUESTS": _int_env(
            CRAWL_CONCURRENT_REQUESTS_ENV_VAR, CRAWL_CONCURRENT_REQUESTS_DEFAULT
        ),
        "CONCURRENT_REQUESTS_PER_DOMAIN": _int_env(
            CRAWL_CONCURRENT_REQUESTS_ENV_VAR, CRAWL_CONCURRENT_REQUESTS_DEFAULT
        ),
        "DOWNLOAD_DELAY": _float_env(
            CRAWL_DOWNLOAD_DELAY_ENV_VAR, CRAWL_DOWNLOAD_DELAY_DEFAULT
        ),
        "DOWNLOAD_TIMEOUT": _int_env(
            CRAWL_REQUEST_TIMEOUT_ENV_VAR, CRAWL_REQUEST_TIMEOUT_SECONDS_DEFAULT
        ),
        "RETRY_TIMES": _int_env(
            CRAWL_RETRY_TIMES_ENV_VAR, CRAWL_RETRY_TIMES_DEFAULT, minimum=0
        ),
        "DOWNLOAD_MAXSIZE": int(size_limit_mb * 1024 * 1024),
        "CLOSESPIDER_PAGECOUNT": _int_env(
            CRAWL_MAX_URLS_ENV_VAR, CRAWL_MAX_URLS_DEFAULT
        ),
        "CLOSESPIDER_TIMEOUT": _int_env(
            CRAWL_TIMEOUT_ENV_VAR, CRAWL_TIMEOUT_SECONDS_DEFAULT
        ),
        # Scrapy treats a DEPTH_LIMIT of 0 as "no limit", so the minimum is 1:
        # the start URL is depth 0 and its own links are depth 1.
        "DEPTH_LIMIT": _int_env(CRAWL_MAX_DEPTH_ENV_VAR, CRAWL_MAX_DEPTH_DEFAULT),
    }


def build_thresholds() -> CrawlThresholds:
    """Build the on-page SEO issue thresholds from the environment."""
    return CrawlThresholds(
        title_min_length=_int_env(
            TITLE_MIN_LENGTH_ENV_VAR, TITLE_MIN_LENGTH_DEFAULT, minimum=0
        ),
        title_max_length=_int_env(
            TITLE_MAX_LENGTH_ENV_VAR, TITLE_MAX_LENGTH_DEFAULT, minimum=1
        ),
        meta_description_min_length=_int_env(
            META_DESCRIPTION_MIN_LENGTH_ENV_VAR,
            META_DESCRIPTION_MIN_LENGTH_DEFAULT,
            minimum=0,
        ),
        meta_description_max_length=_int_env(
            META_DESCRIPTION_MAX_LENGTH_ENV_VAR,
            META_DESCRIPTION_MAX_LENGTH_DEFAULT,
            minimum=1,
        ),
    )


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------
def _registrable_domain(hostname: str) -> str:
    """Return the registrable domain so www and apex hosts are both internal.

    ``suffix_list_urls=()`` makes tldextract use its bundled public-suffix
    snapshot: no HTTP request and no cache directory are needed, which keeps the
    crawl hermetic and works on a read-only home directory.
    """
    extracted = _TLD_EXTRACTOR(hostname.lower())
    if not extracted.domain or not extracted.suffix:
        return hostname.lower()
    return f"{extracted.domain}.{extracted.suffix}"


def _is_internal(url: str, internal_domains: frozenset[str]) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return False
    return _registrable_domain(hostname) in internal_domains


def _split_multi(value: Any, *, keep_empty: bool = False) -> list[str]:
    """Split an advertools ``@@``-joined column into its parts.

    Empty parts are dropped, except for the link text column: an anchor without
    text contributes an empty part, and dropping it would shift every following
    link text one position away from its URL.
    """
    if not isinstance(value, str) or not value:
        return []
    parts = value.split(_LIST_SEPARATOR)
    return parts if keep_empty else [part for part in parts if part]


def _int_or_none(value: Any) -> int | None:
    """Return an integer column value, or None when it is missing or unusable."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_optional_multi(value: Any) -> list[str]:
    """Split a column that advertools omits entirely when it has no values.

    A missing column and a column holding one empty value are different things:
    ``<h1></h1>`` yields an empty string for the single H1 it contains, while a
    page with no H1 at all has no ``h1`` key. Collapsing the empty string to an
    empty list would report a page with an empty H1 as having no H1.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return []
    return value.split(_LIST_SEPARATOR)


def _count_words(text: Any) -> int:
    return len(text.split()) if isinstance(text, str) and text else 0


# --------------------------------------------------------------------------
# Crawl execution
# --------------------------------------------------------------------------
def _start_site_url(site_url: str) -> str:
    """Validate the configured site URL and strip its query and fragment."""
    site_url = (site_url or "").strip()
    if not site_url:
        raise CrawlError("site_url must not be empty")
    parts = urlsplit(site_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise CrawlError(f"site_url must be an absolute http(s) URL, got {site_url!r}")
    if parts.query or parts.fragment:
        logger.warning(
            "Ignoring the query string and fragment of the crawl start URL: %s",
            site_url,
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def run_crawl(site_url: str, output_file: Path) -> None:
    """Run one advertools crawl and write its jsonlines output to ``output_file``."""
    settings = build_crawl_settings()
    start_url = _start_site_url(site_url)
    logger.info(
        "Crawl started site=%s output=%s max_urls=%s timeout=%ss",
        start_url,
        output_file,
        settings["CLOSESPIDER_PAGECOUNT"],
        settings["CLOSESPIDER_TIMEOUT"],
    )
    try:
        with _scrapy_on_path():
            adv.crawl(
                url_list=[start_url],
                output_file=str(output_file),
                follow_links=True,
                allowed_domains=[urlsplit(start_url).hostname],
                exclude_url_params=_list_env(
                    CRAWL_EXCLUDE_URL_PARAMS_ENV_VAR, CRAWL_EXCLUDE_URL_PARAMS_DEFAULT
                ),
                # A fragment never changes the document, so following such links
                # would only produce duplicate rows for the same page.
                exclude_url_regex="#",
                xpath_selectors=_XPATH_SELECTORS,
                keep_columns=list(_CRAWL_COLUMN_PATTERNS),
                custom_settings=settings,
            )
    except Exception as error:  # noqa: BLE001 - re-raised as a CrawlError
        raise CrawlError(f"advertools.crawl() failed for {start_url}: {error}") from error
    if not output_file.exists() or output_file.stat().st_size == 0:
        raise CrawlError(
            f"The crawl of {start_url} produced no output. Check that the site is "
            "reachable, that robots.txt does not block the crawler, and that "
            f"{CRAWL_ROBOTS_TXT_ENV_VAR} is set to a non-blocking value for testing."
        )
    logger.info("Crawl finished site=%s bytes=%d", start_url, output_file.stat().st_size)


# --------------------------------------------------------------------------
# jsonlines parsing
# --------------------------------------------------------------------------
def _response_header(record: dict[str, Any], name: str) -> str:
    """Return a response header value whatever casing the server used.

    advertools stores headers as ``resp_headers_<name>`` using the casing of the
    original response, so ``X-Robots-Tag`` and ``x-robots-tag`` are both possible.
    """
    prefix = f"resp_headers_{name}".lower()
    for key, value in record.items():
        if key.lower() == prefix and isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _robots_directives(*values: str) -> list[str]:
    directives: list[str] = []
    for value in values:
        for directive in value.split(","):
            cleaned = directive.strip().lower()
            if cleaned:
                directives.append(cleaned)
    return directives


def _is_noindex(*values: str) -> bool:
    return any(
        directive in {"noindex", "none"}
        for directive in _robots_directives(*values)
    )


def _build_pages(
    record: dict[str, Any],
    internal_domains: frozenset[str],
) -> list[CrawlPage]:
    """Reduce one jsonlines record to the pages it describes.

    Usually a single page; a URL that was reached through a redirect chain also
    produces the pages it redirected away from.
    """
    url = record.get("url")
    if not isinstance(url, str):
        return []
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return []
    if not _is_internal(url, internal_domains):
        logger.debug("Ignoring out-of-scope crawl record: %s", url)
        return []

    status = _int_or_none(record.get("status"))
    depth = _int_or_none(record.get("depth"))

    titles = _split_multi(record.get("title"))
    meta_descriptions = _split_multi(record.get("meta_desc"))
    canonicals = _split_multi(record.get("canonical"))
    canonical = urljoin(url, canonicals[0].strip()) if canonicals else ""
    # The canonical and hreflang elements are <link href> tags, which Scrapy's
    # LinkExtractor reports as links. They are not page links, so they are
    # removed from the outlink and inlink data of the page.
    ignored_links = {
        normalize_url(urljoin(url, value.strip()))
        for value in (*canonicals, *_split_multi(record.get("alt_href")))
        if value.strip()
    }
    ignored_links.add(normalize_url(url))

    link_urls = _split_multi(record.get("links_url"))
    link_texts = _split_multi(record.get("links_text"), keep_empty=True)
    internal_links: list[str] = []
    internal_link_texts: list[str] = []
    external_links: list[str] = []
    for index, raw_link in enumerate(link_urls):
        link = urljoin(url, raw_link.strip())
        if not link.startswith(("http://", "https://")):
            continue
        if _is_internal(link, internal_domains):
            if normalize_url(link) in ignored_links:
                continue
            internal_links.append(link)
            internal_link_texts.append(
                link_texts[index].strip() if index < len(link_texts) else ""
            )
        else:
            external_links.append(link)

    # Empty H1 elements are kept as empty strings so ``H1 count`` reflects the
    # elements that are really on the page: advertools omits the ``h1`` column
    # when every H1 is empty, so the separate element count decides how many
    # empty H1s to pad the list with.
    h1s = [value.strip() for value in _split_optional_multi(record.get("h1"))]
    h1_count = _int_or_none(record.get("h1_count"))
    if h1_count is not None and h1_count > len(h1s):
        h1s.extend([""] * (h1_count - len(h1s)))
    elif h1_count is not None and h1_count < len(h1s):
        h1s = h1s[:h1_count]

    redirect_urls = _split_multi(record.get("redirect_urls"))
    redirect_reasons = _split_multi(record.get("redirect_reasons"))

    return [
        CrawlPage(
            url=url,
            status=status,
            crawl_error=_clean_crawl_error(record.get("errors")),
            depth=depth,
            title=titles[0].strip() if titles else "",
            meta_description=(
                meta_descriptions[0].strip() if meta_descriptions else ""
            ),
            h1s=h1s,
            canonical=canonical,
            noindex=_is_noindex(
                *_split_multi(record.get("meta_robots")),
                *_split_multi(record.get("meta_googlebot")),
                _response_header(record, "X-Robots-Tag"),
            ),
            content_type=_response_header(record, "Content-Type"),
            # The redirect chain ended on this URL, so the record describes a
            # destination, not a redirect. The hops that point here are built by
            # ``_redirect_pages`` with their own destinations.
            redirect_to="",
            word_count=_count_words(record.get("body_text")),
            internal_links=internal_links,
            internal_link_texts=internal_link_texts,
            external_links=external_links,
        ),
        *_redirect_pages(redirect_urls, redirect_reasons, url, depth),
    ]


def _redirect_pages(
    redirect_urls: list[str],
    redirect_reasons: list[str],
    final_url: str,
    depth: int | None,
) -> list[CrawlPage]:
    """Rebuild the pages that only exist as a hop in a redirect chain.

    Scrapy follows redirects and yields a single item for the final URL, so a
    ``301`` from ``/old`` to ``/new`` would otherwise leave ``/old`` out of every
    report. ``redirect_reasons`` holds the status code of each hop, so the
    intermediate URLs can be restored with their real status and destination
    instead of being guessed.

    A hop is only visible when the destination had not been requested yet: the
    redirect middleware re-queues the target with ``dont_filter`` off, so a
    target that another page already linked to is filtered as a duplicate and
    its redirect metadata never reaches the output file. An old URL that is
    only ever reached through a redirect -- the common case -- is therefore
    reported with its real status, while a URL that is both linked directly and
    redirected to is reported under its final URL only.
    """
    if not redirect_urls:
        return []
    # The chain always ends on the crawled URL: ``redirect_urls`` lists the
    # requested URLs in the order they were left, and this record is where the
    # chain landed.
    chain = [*redirect_urls, final_url]
    pages: list[CrawlPage] = []
    for index, source in enumerate(redirect_urls):
        destination = chain[index + 1]
        # Scrapy stores the hops as absolute URLs, but a hop is resolved the
        # same way as a link so a relative value can never reach the report.
        destination = urljoin(final_url, destination.strip())
        reason = (
            redirect_reasons[index].strip() if index < len(redirect_reasons) else ""
        )
        try:
            status = int(reason)
        except ValueError:
            status = None
        pages.append(
            CrawlPage(
                url=urljoin(final_url, source.strip()),
                status=status,
                depth=depth,
                redirect_to=destination,
            )
        )
    return pages


def _clean_crawl_error(value: Any) -> str:
    """Turn a Scrapy failure representation into a short, readable message.

    ``<twisted.python.failure.Failure scrapy.exceptions.CannotResolveHostError:
    DNS lookup failed: ...>`` becomes ``CannotResolveHostError: DNS lookup
    failed: ...``. Anything else is kept as it arrived.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip().replace("\r", " ").replace("\n", " ")
    match = _TWISTED_FAILURE.match(text)
    if match:
        message = (match.group("message") or "").strip()
        text = f"{match.group('name')}: {message}" if message else match.group("name")
    return text if len(text) <= 300 else f"{text[:297]}..."


def read_pages(output_file: Path, site_url: str) -> list[CrawlPage]:
    """Read the jsonlines crawl output into normalised :class:`CrawlPage` rows.

    Records are parsed and reduced one at a time, so the raw jsonlines payload
    is never held in memory as a whole. Duplicate documents (for example ``/a``
    and ``/a/`` when both resolve to the same page) are kept only once.
    """
    internal_domains = frozenset(
        {_registrable_domain(urlsplit(_start_site_url(site_url)).hostname or "")}
    )
    pages: list[CrawlPage] = []
    seen: set[str] = set()
    malformed = 0

    with output_file.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            for page in _build_pages(record, internal_domains):
                if page.key in seen:
                    logger.debug("Skipping duplicate crawl record for %s", page.url)
                    continue
                seen.add(page.key)
                pages.append(page)

    if malformed:
        logger.warning("Skipped %d malformed crawl record(s)", malformed)
    logger.info("Parsed %d unique internal URL(s) from the crawl output", len(pages))
    return pages


def _require_crawled_pages(pages: list[CrawlPage], site_url: str) -> None:
    """Fail when a crawl produced no usable response at all.

    A failed crawl is invisible in advertools' own reporting: ``scrapy
    runspider`` is started without a return-code check, so a site that cannot be
    reached still leaves a non-empty jsonlines file behind, holding one record
    per URL that could not be fetched with an ``errors`` value instead of a
    status. Uploading five CSVs built from that would turn a broken crawl into a
    clean-looking audit run, so a crawl in which no URL answered is an error.

    A partial failure is not: those URLs are reported in ``internal.csv`` and
    ``issues.csv`` as non-200 internal URLs, which is the useful outcome.
    """
    if not pages:
        raise CrawlError(
            f"The crawl of {site_url} found no internal URL. Check that the site is "
            "reachable, that robots.txt does not block the crawler, and that "
            f"{CRAWL_ROBOTS_TXT_ENV_VAR} is set to a non-blocking value for testing."
        )
    if any(page.status is not None for page in pages):
        return
    errors = sorted({page.crawl_error for page in pages if page.crawl_error})
    detail = "; ".join(errors[:3]) or "no response status was returned"
    raise CrawlError(
        f"The crawl of {site_url} got no response for any of its {len(pages)} URL(s): "
        f"{detail}"
    )


# --------------------------------------------------------------------------
# Report caching
# --------------------------------------------------------------------------
def _crawl_and_cache(site_url: str) -> _CachedCrawl:
    """Crawl one site, build its report, and cache it for the audit run."""
    tmpdir = tempfile.TemporaryDirectory(prefix="advertools-crawl-")
    output_file = Path(tmpdir.name) / "crawl.jl"
    logger.info("Crawl working directory: %s", output_file.parent)
    try:
        run_crawl(site_url, output_file)
        pages = read_pages(output_file, site_url)
        _require_crawled_pages(pages, _start_site_url(site_url))
        report = CrawlReport(pages, thresholds=build_thresholds())
    except Exception as error:  # noqa: BLE001 - cached so we never re-crawl
        tmpdir.cleanup()
        with _CACHE_LOCK:
            _CRAWL_CACHE[site_url] = _CachedCrawl(
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
        _CRAWL_CACHE[site_url] = _CachedCrawl(report=report, cleanup=cleanup)
    logger.info(
        "Crawl report ready site=%s pages=%d",
        site_url,
        report.page_count,
    )
    return _CRAWL_CACHE[site_url]


def _report_for(site_url: str) -> _CachedCrawl:
    with _CACHE_LOCK:
        cached = _CRAWL_CACHE.get(site_url)
    if cached is not None:
        if cached.error is not None:
            raise cached.error
        return cached
    return _crawl_and_cache(site_url)


def fetch_crawl_export(
    *,
    export: str,
    site_url: str,
    start_date: Any = None,
    end_date: Any = None,
) -> dict[str, Any]:
    """Return one crawl report as serialisable rows.

    ``start_date`` and ``end_date`` are accepted so this function matches the
    audit stream interface; a crawl is a point-in-time snapshot of the site. The
    result also carries a ``cleanup`` hook that removes the crawl working
    directory once the caller has uploaded the CSV to Google Drive.
    """
    if export not in CRAWL_EXPORTS:
        choices = ", ".join(sorted(CRAWL_EXPORTS))
        raise ValueError(f"Unknown crawl export {export!r}; choose one of {choices}.")
    del start_date, end_date
    cached = _report_for(site_url)
    return {"rows": cached.report.rows(export), "cleanup": cached.cleanup}
