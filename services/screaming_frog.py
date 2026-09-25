"""Run headless Screaming Frog SEO Spider crawls for the monthly audit.

Screaming Frog writes its exports into a private temporary directory. The
``Internal:All`` export tab CSV and the ``Issues:All`` bulk export's Issues
Overview report are handed to the Google Drive uploader as raw bytes and the
temporary directory is removed right after the uploads (or on any failure),
so no crawl artifact ever persists locally or in the repository.

One crawl per site per run feeds both exports: the internal and issues
streams share a per-site in-memory cache (cleared at the start of every audit
run), mirroring the HTTP crawler's cache in ``services/crawl.py``. The cached
values are bytes, so the temporary directory can be removed after the first
stream uploads without affecting the second.

Note that ``Issues:All`` is a bulk export, not an export tab: it writes one
CSV per issue type into an ``issues_reports/`` folder. We upload only
``issues_overview_report.csv`` (a single summary of every issue found) as the
monthly ``issues_YYYY-MM.csv``; the per-issue detail CSVs are discarded with
the temporary directory.

The executable is resolved from ``SCREAMING_FROG_PATH`` when set (e.g. the
macOS app launcher path) and otherwise from ``screamingfrogseospider`` on
``PATH`` (the Linux CLI), so the same code runs locally on macOS and on
Render/Linux.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from constants.crawl import (
    SCREAMING_FROG_BULK_EXPORTS,
    SCREAMING_FROG_DEFAULT_EXECUTABLE,
    SCREAMING_FROG_EXPORT_TABS,
    SCREAMING_FROG_ISSUES_OVERVIEW_REPORT,
    SCREAMING_FROG_PATH_ENV_VAR,
    SCREAMING_FROG_TIMEOUT_ENV_VAR,
    SCREAMING_FROG_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_MAX_LOG_TAIL = 4_000

_CRAWL_CACHE: dict[str, dict[str, Any]] = {}


class ScreamingFrogError(RuntimeError):
    """Raised when a Screaming Frog crawl cannot be completed."""


def clear_screaming_frog_cache() -> None:
    """Forget cached crawl exports so the next audit re-crawls the sites."""
    _CRAWL_CACHE.clear()


def _resolve_executable() -> str:
    """Return the Screaming Frog CLI path.

    ``SCREAMING_FROG_PATH`` wins when set (absolute path, relative path, or
    a command name on ``PATH``); otherwise the ``screamingfrogseospider``
    command is looked up on ``PATH`` (the Linux install).
    """
    configured = os.getenv(SCREAMING_FROG_PATH_ENV_VAR, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(configured)
        if found:
            return found
        raise ScreamingFrogError(
            f"{SCREAMING_FROG_PATH_ENV_VAR}={configured!r} does not point to a "
            "Screaming Frog executable."
        )
    found = shutil.which(SCREAMING_FROG_DEFAULT_EXECUTABLE)
    if found:
        return found
    raise ScreamingFrogError(
        "Screaming Frog executable not found: set "
        f"{SCREAMING_FROG_PATH_ENV_VAR} to the CLI path or ensure "
        f"{SCREAMING_FROG_DEFAULT_EXECUTABLE!r} is installed on PATH."
    )


def _crawl_timeout() -> int:
    raw = os.getenv(SCREAMING_FROG_TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return SCREAMING_FROG_TIMEOUT_SECONDS
    try:
        return int(raw)
    except ValueError as error:
        raise ScreamingFrogError(
            f"{SCREAMING_FROG_TIMEOUT_ENV_VAR} must be an integer, got {raw!r}"
        ) from error


def _locate_export_csv(output_folder: Path, stem_prefix: str) -> Path:
    """Find the ``<stem_prefix>_all*.csv`` export Screaming Frog wrote."""
    matches = sorted(output_folder.glob(f"{stem_prefix}*.csv"))
    if not matches:
        produced = ", ".join(sorted(p.name for p in output_folder.iterdir()))
        raise ScreamingFrogError(
            f"Screaming Frog produced no {stem_prefix} export in {output_folder}"
            f" (files found: {produced or 'none'})"
        )
    for match in matches:
        if match.stem.lower() == f"{stem_prefix}_all":
            return match
    return matches[0]


def _locate_issues_overview(output_folder: Path) -> Path:
    """Find the Issues Overview report within the ``issues_reports`` folder.

    The ``Issues:All`` bulk export writes one CSV per issue type into an
    ``issues_reports/`` subfolder plus ``issues_overview_report.csv`` — a
    single summary of every issue found (name, type, priority, URL count,
    description and how to fix). We upload only that summary.
    """
    reports_dir = output_folder / "issues_reports"
    if not reports_dir.is_dir():
        raise ScreamingFrogError(
            f"Screaming Frog produced no issues_reports folder in {output_folder}"
        )
    matches = sorted(reports_dir.glob(f"{SCREAMING_FROG_ISSUES_OVERVIEW_REPORT}*.csv"))
    if not matches:
        produced = ", ".join(sorted(p.name for p in reports_dir.iterdir()))
        raise ScreamingFrogError(
            f"Screaming Frog produced no {SCREAMING_FROG_ISSUES_OVERVIEW_REPORT}.csv "
            f"in {reports_dir} (files found: {produced or 'none'})"
        )
    return matches[0]


def _run_crawl(site_url: str, output_folder: Path) -> None:
    executable = _resolve_executable()
    command = [
        executable,
        "--headless",
        "--crawl", site_url,
        "--output-folder", str(output_folder),
        "--export-tabs", SCREAMING_FROG_EXPORT_TABS,
        "--bulk-export", SCREAMING_FROG_BULK_EXPORTS,
    ]
    logger.info("Crawl started site=%s executable=%s", site_url, executable)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_crawl_timeout(),
            check=False,
        )
    except OSError as error:
        raise ScreamingFrogError(
            f"Failed to launch Screaming Frog {executable!r}: {error}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ScreamingFrogError(
            f"Screaming Frog crawl exceeded {_crawl_timeout()}s timeout"
        ) from error
    if completed.returncode != 0:
        logger.error(
            "Crawl failed rc=%d stderr=%s stdout=%s",
            completed.returncode,
            (completed.stderr or "")[-_MAX_LOG_TAIL:],
            (completed.stdout or "")[-_MAX_LOG_TAIL:],
        )
        raise ScreamingFrogError(
            f"Screaming Frog exited with code {completed.returncode} while "
            f"crawling {site_url}; see the logs for the CLI output."
        )
    logger.info("Crawl completed site=%s", site_url)


def _read_export_csv(csv_path: Path, label: str) -> bytes:
    content = csv_path.read_bytes()
    if not content:
        raise ScreamingFrogError(f"Screaming Frog {label} export is empty: {csv_path}")
    logger.info("%s CSV located: %s (%d bytes)", label.capitalize(), csv_path, len(content))
    return content


def _run_and_cache(site_url: str) -> None:
    """Run one crawl, read both exports into memory, and cache them per site.

    Failures are cached too so a second stream does not re-run the crawl just
    to fail again; the temporary directory is removed on failure here.
    """
    tmpdir = tempfile.TemporaryDirectory(prefix="screaming-frog-")
    output_folder = Path(tmpdir.name)
    logger.info("Screaming Frog crawl output folder: %s", output_folder)
    try:
        _run_crawl(site_url, output_folder)
        internal = _read_export_csv(
            _locate_export_csv(output_folder, "internal"), "internal"
        )
        issues = _read_export_csv(
            _locate_issues_overview(output_folder), SCREAMING_FROG_ISSUES_OVERVIEW_REPORT
        )
    except Exception as error:
        tmpdir.cleanup()
        _CRAWL_CACHE[site_url] = {"error": error}
        raise
    cleaned = False

    def cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        tmpdir.cleanup()
        logger.info("Removed Screaming Frog temporary crawl storage: %s", output_folder)

    _CRAWL_CACHE[site_url] = {"internal": internal, "issues": issues, "cleanup": cleanup}


def _exports_for(site_url: str) -> dict[str, Any]:
    cached = _CRAWL_CACHE.get(site_url)
    if cached is None:
        _run_and_cache(site_url)
        cached = _CRAWL_CACHE[site_url]
    if "error" in cached:
        raise cached["error"]  # type: ignore[misc]
    return cached


def fetch_internal_crawl(*, site_url: str, start_date=None, end_date=None) -> dict[str, Any]:
    """Stream-compatible fetch: the ``Internal`` export of this run's crawl.

    Matches the audit runner's stream interface (``fetch(start_date,
    end_date) -> dict``). The returned ``rows`` value is the raw CSV content;
    ``cleanup`` removes the temporary crawl folder after the upload.
    """
    del start_date, end_date  # point-in-time snapshot; dates kept for audit compatibility
    cached = _exports_for(site_url)
    return {"rows": cached["internal"], "cleanup": cached["cleanup"]}


def fetch_issues_crawl(*, site_url: str, start_date=None, end_date=None) -> dict[str, Any]:
    """Stream-compatible fetch: the Issues Overview report of this run's crawl.

    Shares the crawl with :func:`fetch_internal_crawl` through the per-site
    cache, so Screaming Frog runs exactly once per site per run.
    """
    del start_date, end_date  # point-in-time snapshot; dates kept for audit compatibility
    cached = _exports_for(site_url)
    return {"rows": cached["issues"], "cleanup": cached["cleanup"]}