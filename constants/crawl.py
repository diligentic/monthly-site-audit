"""Configuration for the internal crawl stream.

The crawl is executed by `advertools.crawl()` (Scrapy) and every setting below
is overridable through an environment variable so the same code runs on a
laptop and on the Render Free Tier without a rebuild. Defaults are deliberately
conservative: a small download pool, a short per-request timeout and a hard
page-count cap keep CPU and memory low enough for a 512 MB / 0.1 CPU container.
"""

# --------------------------------------------------------------------------
# Environment variables
# --------------------------------------------------------------------------
CRAWL_MAX_URLS_ENV_VAR = "CRAWL_MAX_URLS"
CRAWL_MAX_DEPTH_ENV_VAR = "CRAWL_MAX_DEPTH"
CRAWL_TIMEOUT_ENV_VAR = "CRAWL_TIMEOUT_SECONDS"
CRAWL_REQUEST_TIMEOUT_ENV_VAR = "CRAWL_REQUEST_TIMEOUT_SECONDS"
CRAWL_CONCURRENT_REQUESTS_ENV_VAR = "CRAWL_CONCURRENT_REQUESTS"
CRAWL_DOWNLOAD_DELAY_ENV_VAR = "CRAWL_DOWNLOAD_DELAY"
CRAWL_RETRY_TIMES_ENV_VAR = "CRAWL_RETRY_TIMES"
CRAWL_USER_AGENT_ENV_VAR = "CRAWL_USER_AGENT"
CRAWL_ROBOTS_TXT_ENV_VAR = "CRAWL_ROBOTS_TXT"
CRAWL_RESPONSE_SIZE_LIMIT_MB_ENV_VAR = "CRAWL_RESPONSE_SIZE_LIMIT_MB"
CRAWL_EXCLUDE_URL_PARAMS_ENV_VAR = "CRAWL_EXCLUDE_URL_PARAMS"
CRAWL_LOG_LEVEL_ENV_VAR = "CRAWL_LOG_LEVEL"

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------
CRAWL_MAX_URLS_DEFAULT = 2000
CRAWL_MAX_DEPTH_DEFAULT = 10
CRAWL_TIMEOUT_SECONDS_DEFAULT = 30 * 60
CRAWL_REQUEST_TIMEOUT_SECONDS_DEFAULT = 20
CRAWL_CONCURRENT_REQUESTS_DEFAULT = 4
CRAWL_DOWNLOAD_DELAY_DEFAULT = 0.25
CRAWL_RETRY_TIMES_DEFAULT = 2
CRAWL_USER_AGENT_DEFAULT = "DiligenticSiteAudit/1.0 (+monthly internal SEO audit)"
#: Obeying robots.txt is the polite default; set ``CRAWL_ROBOTS_TXT=0`` only to
#: crawl a site that asks not to be crawled (for example in a local test).
CRAWL_ROBOTS_TXT_DEFAULT = True
CRAWL_RESPONSE_SIZE_LIMIT_MB_DEFAULT = 5.0
#: Scrapy logs crawl progress at DEBUG by default, which is far too noisy for
#: a background audit job. INFO keeps the progress and error lines only.
CRAWL_LOG_LEVEL_DEFAULT = "INFO"

#: Query parameters that would otherwise create duplicate crawlable URLs.
CRAWL_EXCLUDE_URL_PARAMS_DEFAULT: tuple[str, ...] = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
)

# --------------------------------------------------------------------------
# SEO issue thresholds
# --------------------------------------------------------------------------
# The filter boundaries of the historical crawl CSVs already on Google Drive,
# kept so new reports stay comparable with them.
TITLE_MIN_LENGTH_ENV_VAR = "CRAWL_TITLE_MIN_LENGTH"
TITLE_MAX_LENGTH_ENV_VAR = "CRAWL_TITLE_MAX_LENGTH"
META_DESCRIPTION_MIN_LENGTH_ENV_VAR = "CRAWL_META_DESCRIPTION_MIN_LENGTH"
META_DESCRIPTION_MAX_LENGTH_ENV_VAR = "CRAWL_META_DESCRIPTION_MAX_LENGTH"

TITLE_MIN_LENGTH_DEFAULT = 15
TITLE_MAX_LENGTH_DEFAULT = 60
META_DESCRIPTION_MIN_LENGTH_DEFAULT = 70
META_DESCRIPTION_MAX_LENGTH_DEFAULT = 160

# --------------------------------------------------------------------------
# Generated CSV files
# --------------------------------------------------------------------------
# Keep the output key, the user-facing report name and the generated filename
# together so the stream label and the Drive filename cannot drift apart.
CRAWL_EXPORTS: dict[str, tuple[str, str]] = {
    "internal": ("Internal", "internal"),
    "h1": ("H1", "h1"),
    "meta_description": ("Meta Description", "meta_description"),
    "page_titles": ("Page Titles", "page_titles"),
    "issues": ("Issues", "issues"),
}
