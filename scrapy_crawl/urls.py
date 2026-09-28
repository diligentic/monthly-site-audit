"""URL resolution, normalisation, and internal/external classification.

Every URL that enters the crawl -- the start URL, a link on a page, a redirect
target, a ``rel=canonical`` element, or a ``<loc>`` in a sitemap -- is reduced
to one canonical form:

* :func:`resolve_url` makes a URL absolute, lower-cases the scheme and the host,
  and strips the fragment. It is what the crawl requests and what the reports
  print.
* :func:`normalize_url` is :func:`resolve_url` plus one consistent trailing
  slash, so ``/about`` and ``/about/`` are the same document.
* :func:`document_key` drops the scheme as well, which is what tells a
  ``rel=canonical`` that points at the page itself apart from one that points
  somewhere else.

Because the spider, the inlink counts, and the sitemap comparison all use these
functions, a page is fetched once, repeated link shapes collapse into one
inlink, and sitemap membership is tested with the same key the crawl used.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

import tldextract

from scrapy_crawl.errors import CrawlError

#: Only these two schemes are crawlable; ``mailto:``, ``tel:``, ``javascript:``
#: and ``data:`` links are reported as text but never requested.
HTTP_SCHEMES = frozenset({"http", "https"})

#: Public-suffix lookups run against the snapshot bundled with tldextract, so
#: resolving "www.example.com" and "example.com" to one internal domain needs no
#: network access and no on-disk cache directory (which Render's ephemeral
#: filesystem would lose on every deploy anyway).
_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(hostname: str) -> str:
    """Return the registrable domain of a host name.

    ``www.example.com``, ``example.com``, and ``blog.example.com`` all resolve
    to ``example.com``, so one host group covers them. A host without a public
    suffix (``localhost``, an IP address) is returned lower-cased as it is.
    """
    host = (hostname or "").strip().lower()
    extracted = _TLD_EXTRACTOR(host)
    if not extracted.domain or not extracted.suffix:
        return host
    return f"{extracted.domain}.{extracted.suffix}"


def resolve_url(url: str, base: str | None = None) -> str:
    """Return ``url`` as an absolute http(s) URL with no fragment.

    ``base`` resolves a relative reference the way a browser would. Anything
    that is not usable as a page address -- an empty href, a fragment-only
    link, ``mailto:``, a malformed port -- returns an empty string, so callers
    can simply skip the result.

    A fragment is always dropped: it addresses a position inside a document,
    never a different document, and keeping it would create a second row for
    the same page.
    """
    candidate = (url or "").strip()
    if not candidate:
        return ""
    if base:
        try:
            candidate = urljoin(base, candidate)
        except ValueError:  # pragma: no cover - urljoin is total for valid input
            return ""
    try:
        parts = urlsplit(candidate)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        # A hand-written href can be malformed enough to upset the parser; it
        # is a dead link, not a crawl error.
        return ""
    if scheme not in HTTP_SCHEMES or not host:
        return ""
    netloc = f"{host}:{port}" if port else host
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def normalize_url(url: str) -> str:
    """Return the comparison key for ``url``.

    A trailing slash is kept only for the site root, so ``/about/`` and
    ``/about`` share one key. Used for duplicate filtering, inlink counting,
    and sitemap membership -- never for printing an address.
    """
    resolved = resolve_url(url)
    if not resolved:
        return ""
    parts = urlsplit(resolved)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", parts.query, "")
    )


def document_key(url: str) -> str:
    """Return a scheme-insensitive key for the document ``url`` addresses.

    A page served over ``http`` that canonicalises to its own ``https`` version
    is not pointing somewhere else, so the scheme must not be part of the
    comparison that decides between ``Indexable`` and ``Canonicalised``.
    """
    resolved = resolve_url(url)
    if not resolved:
        return ""
    parts = urlsplit(resolved)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("", parts.netloc, path, parts.query, ""))


def same_document(first: str, second: str) -> bool:
    """Return whether two URLs address the same document."""
    first_key = document_key(first)
    return bool(first_key) and first_key == document_key(second)


def internal_domains_for(site_url: str) -> frozenset[str]:
    """Return the host groups that count as internal for ``site_url``."""
    host = urlsplit(site_url).hostname or ""
    if not host:
        return frozenset()
    return frozenset({registrable_domain(host)})


def is_internal(url: str, internal_domains: frozenset[str]) -> bool:
    """Return whether ``url`` belongs to one of the internal host groups."""
    if not internal_domains:
        return False
    host = urlsplit(url).hostname or ""
    if not host:
        return False
    return registrable_domain(host) in internal_domains


def clean_start_url(url: str) -> str:
    """Validate a configured site URL and return it normalised.

    The query string and the fragment of the start URL are dropped: a crawl
    starts at the site root or at a clean page, never at a tracked variant of
    it.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise CrawlError("The crawl site URL must not be empty")
    resolved = resolve_url(candidate)
    if not resolved:
        raise CrawlError(
            f"The crawl site URL must be an absolute http(s) URL, got {url!r}"
        )
    parts = urlsplit(resolved)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
