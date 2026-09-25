"""Run the required headless Screaming Frog SEO Spider exports.

Screaming Frog crawls each site once and writes the Internal, H1, Meta
Description, Page Titles, and Images ``:All`` export tabs plus the Issues
Overview bulk report. The generated CSVs are read into memory, uploaded to
Google Drive by the audit runner, and then removed with the private temporary
crawl directory. No crawl artifact is persisted locally.

All required exports share a per-site cache so a crawl is launched only once
for a site during an audit run, even when several output files or historical
months are being stored. The cache is cleared before every run.
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
    SCREAMING_FROG_EXPORTS,
    SCREAMING_FROG_ISSUES_OVERVIEW_REPORT,
    SCREAMING_FROG_PATH_ENV_VAR,
    SCREAMING_FROG_TAB_EXPORTS,
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


def _locate_export_csv(output_folder: Path, export: str) -> Path:
    """Find the CSV produced for a Screaming Frog ``:All`` export tab."""
    try:
        tab_name, stem = SCREAMING_FROG_TAB_EXPORTS[export]
    except KeyError as error:
        raise ScreamingFrogError(
            f"{export!r} is not a Screaming Frog export tab"
        ) from error
    matches = sorted(
        path
        for path in output_folder.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".csv"
        and path.stem.lower().startswith(f"{stem.lower()}_")
    )
    if not matches:
        produced = ", ".join(sorted(path.name for path in output_folder.iterdir()))
        raise ScreamingFrogError(
            f"Screaming Frog produced no {tab_name} export in {output_folder} "
            f"(files found: {produced or 'none'})"
        )
    expected_stem = f"{stem}_all".lower()
    for match in matches:
        if match.stem.lower() == expected_stem:
            return match
    return matches[0]


def _locate_issues_overview(output_folder: Path) -> Path:
    """Find the Issues Overview report written by the ``Issues:All`` export."""
    reports_dir = output_folder / "issues_reports"
    if not reports_dir.is_dir():
        raise ScreamingFrogError(
            f"Screaming Frog produced no issues_reports folder in {output_folder}"
        )
    matches = sorted(
        reports_dir.glob(f"{SCREAMING_FROG_ISSUES_OVERVIEW_REPORT}*.csv")
    )
    if not matches:
        produced = ", ".join(sorted(path.name for path in reports_dir.iterdir()))
        raise ScreamingFrogError(
            "Screaming Frog produced no "
            f"{SCREAMING_FROG_ISSUES_OVERVIEW_REPORT}.csv in {reports_dir} "
            f"(files found: {produced or 'none'})"
        )
    return matches[0]


def _run_crawl(site_url: str, output_folder: Path) -> None:
    executable = _resolve_executable()
    command = [
        executable,
        "--headless",
        "--crawl",
        site_url,
        "--output-folder",
        str(output_folder),
        "--export-tabs",
        SCREAMING_FROG_EXPORT_TABS,
        "--bulk-export",
        SCREAMING_FROG_BULK_EXPORTS,
    ]
    timeout = _crawl_timeout()
    logger.info(
        "Crawl started site=%s executable=%s export_tabs=%s bulk_exports=%s",
        site_url,
        executable,
        SCREAMING_FROG_EXPORT_TABS,
        SCREAMING_FROG_BULK_EXPORTS,
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except OSError as error:
        raise ScreamingFrogError(
            f"Failed to launch Screaming Frog {executable!r}: {error}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ScreamingFrogError(
            f"Screaming Frog crawl exceeded {timeout}s timeout"
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


def _read_export_csv(csv_path: Path, tab_name: str) -> bytes:
    content = csv_path.read_bytes()
    if not content:
        raise ScreamingFrogError(
            f"Screaming Frog {tab_name} export is empty: {csv_path}"
        )
    logger.info(
        "%s CSV located: %s (%d bytes)", tab_name, csv_path, len(content)
    )
    return content


def _run_and_cache(site_url: str) -> None:
    """Run one crawl, read all required exports, and cache them per site.

    Failures are cached too so another export stream does not launch the same
    crawl again just to fail. The temporary directory is removed immediately
    on failure.
    """
    tmpdir = tempfile.TemporaryDirectory(prefix="screaming-frog-")
    output_folder = Path(tmpdir.name)
    logger.info("Screaming Frog crawl output folder: %s", output_folder)
    try:
        _run_crawl(site_url, output_folder)
        exports = {
            export: _read_export_csv(
                _locate_export_csv(output_folder, export), tab_name
            )
            for export, (tab_name, _) in SCREAMING_FROG_TAB_EXPORTS.items()
        }
        exports["issues"] = _read_export_csv(
            _locate_issues_overview(output_folder),
            SCREAMING_FROG_ISSUES_OVERVIEW_REPORT,
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
        logger.info(
            "Removed Screaming Frog temporary crawl storage: %s", output_folder
        )

    _CRAWL_CACHE[site_url] = {**exports, "cleanup": cleanup}


def _exports_for(site_url: str) -> dict[str, Any]:
    cached = _CRAWL_CACHE.get(site_url)
    if cached is None:
        _run_and_cache(site_url)
        cached = _CRAWL_CACHE[site_url]
    if "error" in cached:
        raise cached["error"]  # type: ignore[misc]
    return cached


def fetch_screaming_frog_export(
    *,
    export: str,
    site_url: str,
    start_date=None,
    end_date=None,
) -> dict[str, Any]:
    """Return one required Screaming Frog export as raw CSV bytes.

    Dates are accepted so this function matches the audit stream interface;
    Screaming Frog exports are point-in-time crawl snapshots.
    """
    if export not in SCREAMING_FROG_EXPORTS:
        choices = ", ".join(sorted(SCREAMING_FROG_EXPORTS))
        raise ValueError(
            f"Unknown Screaming Frog export {export!r}; choose one of {choices}."
        )
    del start_date, end_date
    cached = _exports_for(site_url)
    return {"rows": cached[export], "cleanup": cached["cleanup"]}


def fetch_internal_crawl(
    *, site_url: str, start_date=None, end_date=None
) -> dict[str, Any]:
    """Backward-compatible stream wrapper for the ``Internal`` export."""
    return fetch_screaming_frog_export(
        export="internal",
        site_url=site_url,
        start_date=start_date,
        end_date=end_date,
    )


def fetch_issues_crawl(
    *, site_url: str, start_date=None, end_date=None
) -> dict[str, Any]:
    """Backward-compatible stream wrapper for the Issues Overview report."""
    return fetch_screaming_frog_export(
        export="issues",
        site_url=site_url,
        start_date=start_date,
        end_date=end_date,
    )
