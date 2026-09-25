MAX_URLS = 5_000
MAX_IMAGES = 10_000
CRAWL_TIMEOUT_SECONDS = 1_800
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.5
MAX_REDIRECTS = 10
MAX_RETRIES = 2
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
USER_AGENT = "MonthlySiteAuditBot/1.0"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

SCREAMING_FROG_PATH_ENV_VAR = "SCREAMING_FROG_PATH"
SCREAMING_FROG_TIMEOUT_ENV_VAR = "SCREAMING_FROG_TIMEOUT_SECONDS"
SCREAMING_FROG_DEFAULT_EXECUTABLE = "screamingfrogseospider"
SCREAMING_FROG_TIMEOUT_SECONDS = 60 * 60
SCREAMING_FROG_EXPORT_TABS = "Internal:All"
SCREAMING_FROG_BULK_EXPORTS = "Issues:All"
SCREAMING_FROG_ISSUES_OVERVIEW_REPORT = "issues_overview_report"

CRAWL_COLUMNS = (
    "URL", "Final URL", "Status Code", "Final Status Code", "Reason", "Content Type",
    "Indexability", "Indexability Status", "Canonical Link Element 1",
    "Unique Inlinks", "Redirect URL", "Crawl Truncated",
)
IMAGE_COLUMNS = (
    "Image URL", "Status Code", "Content Type", "File Size", "Width",
    "Height", "Error",
)
