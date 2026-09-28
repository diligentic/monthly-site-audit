"""Duplicate filtering on the normalised URL.

Scrapy de-duplicates requests by fingerprint. With its default fingerprinter,
``/about`` and ``/about/`` are two different documents, so a site that links to
both shapes gets fetched twice and reported twice. This fingerprinter hashes the
*normalised* URL instead (see :mod:`scrapy_crawl.urls`), which is the same form
used for inlink counting and sitemap membership, so one document is one request
and one row.

A request with ``meta['verbatim_url']`` keeps Scrapy's own fingerprint, so
callers that need a byte-exact URL can still ask for it.
"""

from __future__ import annotations

import hashlib

from scrapy import Request
from scrapy.utils.request import RequestFingerprinter

from scrapy_crawl.urls import normalize_url

#: Separates the hashed parts so a URL can never be crafted to collide with a
#: different method or body.
_FIELD_SEPARATOR = "\x00"


class NormalizedRequestFingerprinter(RequestFingerprinter):
    """Fingerprint requests by method, normalised URL, and body."""

    def fingerprint(self, request: Request) -> bytes:
        if request.meta.get("verbatim_url"):
            return super().fingerprint(request)
        parts = [
            request.method.upper(),
            normalize_url(request.url),
            hashlib.sha1(request.body or b"").hexdigest(),
        ]
        return hashlib.sha1(_FIELD_SEPARATOR.join(parts).encode("utf-8")).digest()
