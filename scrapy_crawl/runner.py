"""Run a crawl, either in this process or in a child process.

The crawl is run in a child process by default. That is not defensiveness for
its own sake: Scrapy's reactor is a process-wide, single-use object. Running a
crawl inside the web service would install a Twisted reactor next to
Uvicorn's event loop, and the first crawl would take the process's HTTP server
with it when it finished. A child process gives the crawler its own
interpreter, its own reactor, and a clean exit code, and it means the crawler's
memory is returned to the container the moment it ends instead of being held by
a long-lived service.

The child is this module's own command line interface, so the exact same
command works locally and on the server::

    python -m scrapy_crawl --site https://example.com --output crawl.jsonl
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from scrapy_crawl.errors import CrawlError
from scrapy_crawl.settings import CRAWL_SHUTDOWN_GRACE_SECONDS, crawl_timeout_seconds

logger = logging.getLogger(__name__)

#: The project root, so ``python -m scrapy_crawl`` resolves no matter which
#: directory the service or the CLI was started from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Scrapy logs one line per request at INFO; the tail of the child's output is
#: what explains a crawl that came back short.
_LOG_TAIL_LINES = 40

#: Scrapy writes ``[scrapy.core.engine] WARNING: ...`` to stderr. That marker is
#: the only thing that says how bad a line is, and the two formats differ
#: (``INFO:`` with a colon, and again without one on a duplicate handler), so
#: both are matched.
_SCRAPY_LEVEL = re.compile(r"\]\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b:?")

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.ERROR,
}


def _levels(output: str, *, default: int = logging.INFO) -> list[tuple[int, str]]:
    """Return ``(level, line)`` for every non-empty line of ``output``."""
    tagged: list[tuple[int, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        match = _SCRAPY_LEVEL.search(line)
        level = _LOG_LEVELS.get(match.group(1), default) if match else default
        tagged.append((level, line))
    return tagged


def python_executable() -> str:
    """Return the interpreter to run the crawl with.

    The running interpreter is used rather than ``python`` from ``PATH``: on a
    deployed service the virtual environment is the one that has Scrapy
    installed, and ``PATH`` may not include it.
    """
    return sys.executable or "python3"


def build_crawl_command(
    site_url: str,
    output_file: Path,
    *,
    sitemap_url: str | None = None,
) -> list[str]:
    """Return the command line that crawls ``site_url`` into ``output_file``."""
    command = [
        python_executable(),
        "-m",
        "scrapy_crawl",
        "--site",
        site_url,
        "--output",
        str(output_file),
    ]
    if sitemap_url:
        command += ["--sitemap", sitemap_url]
    return command


def run_crawl(
    site_url: str,
    output_file: Path,
    *,
    sitemap_url: str | None = None,
    timeout_seconds: int | None = None,
) -> None:
    """Crawl ``site_url`` in a child process and write the feed to ``output_file``.

    Args:
        site_url: the site to crawl.
        output_file: the JSONL feed to write; its directory must exist.
        sitemap_url: the sitemap to read for the "In Sitemap" column.
        timeout_seconds: how long the child may run in total. Defaults to the
            in-spider wall-clock cap plus a shutdown allowance, so a crawl that
            finished on its own terms is never killed by this.

    Raises:
        CrawlError: if the child could not be started, exited with an error, or
            produced no feed.
    """
    output_file = Path(output_file)
    if not output_file.parent.is_dir():
        raise CrawlError(
            f"The crawl output directory does not exist: {output_file.parent}"
        )
    require_scrapy()
    limit = timeout_seconds or (crawl_timeout_seconds() + CRAWL_SHUTDOWN_GRACE_SECONDS)
    command = build_crawl_command(site_url, output_file, sitemap_url=sitemap_url)
    logger.info(
        "Crawl starting site=%s output=%s timeout=%ss", site_url, output_file, limit
    )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=str(PROJECT_ROOT),
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=limit,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise CrawlError(
            f"The crawl of {site_url} did not finish within {limit}s and was stopped. "
            f"Raise CRAWL_TIMEOUT_SECONDS if the site is genuinely this slow, or lower "
            "CRAWL_MAX_URLS if the site is much larger than the budget."
        ) from error
    except OSError as error:
        raise CrawlError(
            f"The crawl of {site_url} could not be started: {error}"
        ) from error

    _log_output(completed.stdout, completed.stderr, failed=completed.returncode != 0)
    if completed.returncode != 0:
        raise CrawlError(
            f"The crawl of {site_url} failed with exit code {completed.returncode}."
            f"{_failure_detail(completed.stderr or completed.stdout)}"
        )
    if not output_file.exists() or output_file.stat().st_size == 0:
        raise CrawlError(
            f"The crawl of {site_url} produced no output at {output_file}. Check that "
            "the site is reachable, that robots.txt allows the crawler, and that the "
            "spider logged no error."
        )
    logger.info("Crawl finished site=%s bytes=%d", site_url, output_file.stat().st_size)


def _child_env() -> dict[str, str]:
    """Return the environment for the child process.

    ``PYTHONUNBUFFERED`` matters: a buffered crawler log is useless when the
    process is killed on a timeout, which is exactly when the log is needed.
    """
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _log_output(stdout: str, stderr: str, *, failed: bool) -> None:
    """Log the child's output at the level it was written at, keeping it readable.

    A crawl is a long job whose only visible trace is this log, so the summary
    and every error are surfaced. Scrapy writes everything to stderr and its own
    ``INFO``/``WARNING`` markers are the only reliable signal of how bad a line
    is, so those markers decide the level: a crawl that worked must not fill the
    service log with ``ERROR`` lines, and a crawl that failed must not hide its
    error among ``INFO`` lines.

    Only the tail of each stream is kept, because a few hundred "downloading"
    lines are not worth a log page per site per month; the count of what was
    dropped is logged so a short log is never mistaken for a quiet crawl.
    """
    omitted = 0
    for stream, fallback in ((stdout, logging.INFO), (stderr, logging.INFO)):
        lines = _levels(stream or "", default=fallback)
        omitted = max(omitted, len(lines) - _LOG_TAIL_LINES)
        for level, line in lines[-_LOG_TAIL_LINES:]:
            logger.log(level, "crawl | %s", line)
    if omitted > 0:
        logger.log(
            logging.ERROR if failed else logging.INFO,
            "crawl | (%d earlier line(s) omitted)",
            omitted,
        )


def _failure_detail(output: str) -> str:
    """Return the last error lines of a failed crawl, for the exception message."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return f" Last output: {' | '.join(lines[-3:])}"


def require_scrapy() -> None:
    """Fail early and clearly when Scrapy is not importable.

    A missing dependency inside the child process would otherwise show up as a
    non-zero exit code with a stack trace, or -- worse -- as an empty feed that
    looks like a site with no pages.
    """
    try:
        import scrapy  # noqa: F401
    except ImportError as error:  # pragma: no cover - dependency is declared
        raise CrawlError(
            "Scrapy is not installed. Install the project dependencies (uv sync)."
        ) from error
