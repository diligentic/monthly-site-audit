MAX_URLS = 5_000
MAX_IMAGES = 10_000
CRAWL_TIMEOUT_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 0.5
MAX_REDIRECTS = 10
MAX_RETRIES = 2
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
USER_AGENT = "MonthlySiteAuditBot/1.0"
CRAWL_STORAGE_ROOT_ENV_VAR = "CRAWL_STORAGE_ROOT"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

CRAWL_COLUMNS = (
    "URL", "Final URL", "Status Code", "Final Status Code", "Reason", "Content Type",
    "Indexability", "Indexability Status", "Canonical Link Element 1",
    "Unique Inlinks", "Redirect URL", "Crawl Truncated",
)
IMAGE_COLUMNS = (
    "Image URL", "Status Code", "Content Type", "File Size", "Width",
    "Height", "Error",
)
