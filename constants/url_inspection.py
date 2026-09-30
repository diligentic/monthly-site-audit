"""Quota limits and environment configuration for the URL Inspection API.

The URL Inspection API is charged per inspected URL against a per-project quota
(600 requests per minute, 2000 per day). Those numbers are the reason this file
exists: the client cannot simply fire one request per page as fast as the thread
pool allows, because a 600-page site at 8 concurrent workers would land at
several thousand requests per minute and be throttled. Everything the client
needs to stay under the quota is therefore configurable here and read from the
environment, so an operator can slow a run down without a code change.

The documented limits are kept as named constants so the defaults below can be
clamped to them rather than trusted blindly.
"""

#: Google's published per-project quota for ``urlInspection/index:inspect``.
URL_INSPECTION_QUOTA_PER_MINUTE = 600
URL_INSPECTION_QUOTA_PER_DAY = 2_000

# --------------------------------------------------------------------------
# Environment configuration
# --------------------------------------------------------------------------
URL_INSPECTION_ENABLED_ENV_VAR = "GSC_URL_INSPECTION_ENABLED"
URL_INSPECTION_ENABLED_DEFAULT = True

#: Whether the sitemap's own URLs join the crawled pages in the report. On by
#: default: a sitemap URL the crawl never reached is usually the one whose
#: indexing needs checking, and it costs one more request per URL against the
#: same quota the report already spends. Set to ``0`` to inspect the crawled
#: pages only. Note that the URLs come from the crawl's own sitemap read, so
#: ``CRAWL_SITEMAP=0`` disables them here too.
URL_INSPECTION_SITEMAP_URLS_ENV_VAR = "GSC_URL_INSPECTION_SITEMAP_URLS"
URL_INSPECTION_SITEMAP_URLS_DEFAULT = True

#: Number of URLs inspected at once. The calls are I/O bound, so threads are
#: enough; the rate limiter, not the pool size, is what actually paces the run.
URL_INSPECTION_CONCURRENCY_ENV_VAR = "GSC_URL_INSPECTION_CONCURRENCY"
URL_INSPECTION_CONCURRENCY_DEFAULT = 8

#: Ceiling on requests per minute. Deliberately below the documented 600 so that
#: a run that is sharing a project with another tool does not get throttled.
URL_INSPECTION_REQUESTS_PER_MINUTE_ENV_VAR = "GSC_URL_INSPECTION_REQUESTS_PER_MINUTE"
URL_INSPECTION_REQUESTS_PER_MINUTE_DEFAULT = 500

#: Hard cap on how many URLs one run inspects, across the crawled pages and the
#: sitemap URLs together. A site can return more URLs than the daily quota
#: allows, and a truncated CSV that is silently missing URLs is worse than a
#: small one that is documented as capped. The crawled pages are selected first,
#: so raising the cap is what brings the sitemap-only URLs in.
URL_INSPECTION_MAX_URLS_ENV_VAR = "GSC_URL_INSPECTION_MAX_URLS"
URL_INSPECTION_MAX_URLS_DEFAULT = 1_000
