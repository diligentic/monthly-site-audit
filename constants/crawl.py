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
