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

# Keep the output key, user-facing report/tab name, and generated-file stem
# together so the CLI command and Drive filenames cannot drift apart. Issues
# is a bulk report rather than an export tab in Screaming Frog.
SCREAMING_FROG_TAB_EXPORTS = {
    "internal": ("Internal", "internal"),
    "h1": ("H1", "h1"),
    "meta_description": ("Meta Description", "meta_description"),
    "page_titles": ("Page Titles", "page_titles"),
    "images": ("Images", "images"),
}
SCREAMING_FROG_EXPORTS = {
    **SCREAMING_FROG_TAB_EXPORTS,
    "issues": ("Issues", "issues"),
}
SCREAMING_FROG_EXPORT_TABS = ",".join(
    f"{tab_name}:All" for tab_name, _ in SCREAMING_FROG_TAB_EXPORTS.values()
)
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
