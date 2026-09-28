"""Reading XML sitemaps.

A sitemap is XML that somebody else wrote, so it is parsed as untrusted input.
``defusedxml`` refuses entity expansion ("billion laughs") and external entity
references, either of which could otherwise make the crawler spend its memory
reading a file instead of a page. It is a hard dependency of this project, so
there is nothing to configure; the standard library is only used for the one
thing ``defusedxml`` re-exports from it, the ``Element`` type and
``ParseError``.

Every failure mode -- an empty body, an HTML error page served in place of a
sitemap, a truncated download, a refused entity -- comes back as
:class:`SitemapError` with a short message. The spider turns that into one
warning and leaves the "In Sitemap" column unknown, which is the honest result:
a crawl that could not read the sitemap knows nothing about sitemap membership.
"""

from __future__ import annotations

import logging

from defusedxml.ElementTree import fromstring as _safe_fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import Element, ParseError

logger = logging.getLogger(__name__)

#: Namespace-free names of the two sitemap document roots.
URLSET = "urlset"
SITEMAP_INDEX = "sitemapindex"

#: Elements that hold one sitemap entry, in either document type.
_ENTRY_NAMES = frozenset({"url", "sitemap"})

#: The two document roots a sitemap may have. Anything else is not a sitemap --
#: most often an HTML error page served with a 200, which parses as perfectly
#: good XML and would otherwise be read as an empty sitemap, turning "we do not
#: know" into "this page is not in the sitemap" for every row in the report.
_ROOT_NAMES = frozenset({URLSET, SITEMAP_INDEX})


class SitemapError(Exception):
    """Raised when a sitemap cannot be read."""


def local_name(tag: object) -> str:
    """Return an element's tag name without its XML namespace, lower-cased.

    Sitemaps are published under ``http://www.sitemaps.org/schemas/sitemap/0.9``,
    and the same document is frequently served with no namespace at all (and
    sometimes with a vendor one). Comparing the local name works for all three,
    which is why the namespace is dropped rather than required.
    """
    if not isinstance(tag, str):
        # A comment or a processing instruction has a callable tag.
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap_xml(body: bytes, *, source: str) -> Element:
    """Parse ``body`` as a sitemap document and return its root element.

    Args:
        body: the raw response body, before any decoding.
        source: the URL the body came from, used only in the error message.

    Returns:
        The root element, which the caller classifies with :func:`is_index`.

    Raises:
        SitemapError: the body is empty, is not well-formed XML, uses
            constructs that are refused for safety, or is well-formed XML that
            is not a sitemap at all.
    """
    if not body or not body.strip():
        raise SitemapError("the file is empty")
    try:
        root = _safe_fromstring(body)
    except ParseError as error:
        raise SitemapError(f"the file is not well-formed XML ({error})") from error
    except DefusedXmlException as error:
        raise SitemapError(f"the file uses a refused XML construct ({error})") from error
    name = local_name(root.tag)
    if name not in _ROOT_NAMES:
        raise SitemapError(f"the root element is <{name or '?'}>, not a sitemap")
    return root


def is_index(root: Element) -> bool:
    """Return whether ``root`` is a ``<sitemapindex>`` rather than a ``<urlset>``.

    A sitemap index lists further sitemap files; a urlset lists pages. Only a
    urlset says anything about whether a page belongs in a sitemap, which is
    why the two are reported differently.
    """
    return local_name(root.tag) == SITEMAP_INDEX


def locations(root: Element, *, limit: int, source: str) -> list[str]:
    """Return the ``<loc>`` values of one sitemap, in document order.

    Args:
        root: the element returned by :func:`parse_sitemap_xml`.
        limit: the most entries to return. A sitemap claiming millions of URLs
            is a broken file, and reading it would cost more than the whole
            crawl's budget; what was dropped is logged, not silently shortened.
        source: the URL the sitemap came from, used only in the log message.
    """
    found: list[str] = []
    truncated = False
    for node in root:
        if local_name(node.tag) not in _ENTRY_NAMES:
            continue
        for child in node:
            if local_name(child.tag) == "loc" and child.text:
                found.append(" ".join(child.text.split()))
                break
        if len(found) > limit:
            # One past the cap, so a sitemap of exactly ``limit`` entries is not
            # reported as truncated.
            truncated = True
            break
    if truncated:
        logger.warning(
            "The sitemap at %s lists more than %d entries; the rest were ignored.",
            source,
            limit,
        )
    return found[:limit]
