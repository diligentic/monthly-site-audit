"""Indexability rules for one crawled URL.

The six rules are evaluated in a fixed order, and the first one that matches
wins:

1. a 3xx status  -> ``Non-Indexable`` / ``Redirected``
2. a non-HTML content type -> ``Non-Indexable`` / ``Non-HTML``
3. a status other than 200 -> ``Non-Indexable`` / ``Non-200 (<code>)``
4. ``noindex`` in ``<meta name="robots">`` or in ``X-Robots-Tag``
   -> ``Non-Indexable`` / ``Noindex``
5. a ``rel=canonical`` that points at another document
   -> ``Non-Indexable`` / ``Canonicalised``
6. otherwise -> ``Indexable``

The order matters: a 404 page that also carries ``noindex`` is a broken page
first, and a PDF is a non-HTML document whether or not it is missing. Only the
first matching reason is reported, so ``Indexability Status`` always names the
single reason a page is out of the index.

This module is the only implementation of those rules. The spider calls it
while crawling (so the JSONL feed carries the verdict) and the report layer
calls it again while writing the CSV, from the same pure inputs, so the feed
and the CSV can never disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scrapy_crawl.urls import same_document

#: Values of the ``Indexability`` column.
INDEXABLE = "Indexable"
NON_INDEXABLE = "Non-Indexable"

#: Values of the ``Indexability Status`` column.
STATUS_INDEXABLE = "Indexable"
STATUS_REDIRECTED = "Redirected"
STATUS_NON_HTML = "Non-HTML"
STATUS_NOINDEX = "Noindex"
STATUS_CANONICALISED = "Canonicalised"

#: The 3xx codes that are redirects. ``304 Not Modified`` is also 3xx but is a
#: cache revalidation, not a redirect, so it falls through to ``Non-200``.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: A missing content type is treated as HTML: plenty of servers send no
#: ``Content-Type`` at all, and nothing in the document suggests otherwise.
_HTML_CONTENT_TYPE = re.compile(r"html|xhtml", re.IGNORECASE)


def is_html(content_type: str) -> bool:
    """Return whether ``content_type`` describes an HTML document."""
    if not content_type or not content_type.strip():
        return True
    return bool(_HTML_CONTENT_TYPE.search(content_type))


def non_200_status(status: int) -> str:
    """Return the ``Indexability Status`` value for a non-200 response."""
    return f"Non-200 ({status})"


@dataclass(frozen=True)
class Indexability:
    """The indexability verdict for one URL.

    Attributes:
        indexability: ``Indexable`` or ``Non-Indexable``.
        status: the single reason, using the status vocabulary above.
        reason: a human-readable detail for the same verdict (empty when the
            page is indexable, which needs no explanation).
    """

    indexability: str
    status: str
    reason: str = ""


def evaluate_indexability(
    *,
    url: str,
    status: int,
    content_type: str = "",
    noindex: bool = False,
    canonical: str = "",
    error: str = "",
) -> Indexability:
    """Return the indexability verdict for one crawled URL.

    Args:
        url: the address that was requested, as crawled.
        status: the HTTP status code, or ``0`` when the request failed.
        content_type: the raw ``Content-Type`` response header.
        noindex: whether a ``noindex`` robots directive was found, in the meta
            tags or in the ``X-Robots-Tag`` header.
        canonical: the resolved absolute ``rel=canonical`` URL, if any.
        error: the crawler error message for a failed request, if any.
    """
    if status in REDIRECT_STATUSES:
        return Indexability(NON_INDEXABLE, STATUS_REDIRECTED, f"HTTP {status}")
    if not is_html(content_type):
        detail = f"Content-Type: {content_type.strip()}" if content_type.strip() else ""
        return Indexability(NON_INDEXABLE, STATUS_NON_HTML, detail)
    if status != 200:
        if error:
            return Indexability(
                NON_INDEXABLE, non_200_status(status), f"Request failed: {error}"
            )
        return Indexability(NON_INDEXABLE, non_200_status(status), f"HTTP {status}")
    if noindex:
        return Indexability(
            NON_INDEXABLE, STATUS_NOINDEX, "noindex robots directive"
        )
    if canonical and not same_document(canonical, url):
        return Indexability(
            NON_INDEXABLE, STATUS_CANONICALISED, f"Canonical: {canonical}"
        )
    return Indexability(INDEXABLE, STATUS_INDEXABLE)
