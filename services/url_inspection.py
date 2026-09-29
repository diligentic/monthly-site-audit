import logging
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

import requests

from constants.api_urls import (
    GOOGLE_URL_INSPECTION_BASE_URL,
    URL_INSPECTION_INSPECT_PATH,
)
from constants.url_inspection import (
    URL_INSPECTION_CONCURRENCY_DEFAULT,
    URL_INSPECTION_CONCURRENCY_ENV_VAR,
    URL_INSPECTION_ENABLED_DEFAULT,
    URL_INSPECTION_ENABLED_ENV_VAR,
    URL_INSPECTION_MAX_URLS_DEFAULT,
    URL_INSPECTION_MAX_URLS_ENV_VAR,
    URL_INSPECTION_QUOTA_PER_MINUTE,
    URL_INSPECTION_REQUESTS_PER_MINUTE_DEFAULT,
    URL_INSPECTION_REQUESTS_PER_MINUTE_ENV_VAR,
)
from services.oauth import get_access_token
from utils.get_env import env_bool, env_int
from utils.http import REQUEST_TIMEOUT_SECONDS, build_retry_session
from utils.url_inspection_csv import URL_INSPECTION_COLUMNS

logger = logging.getLogger(__name__)

INSPECT_URL = f"{GOOGLE_URL_INSPECTION_BASE_URL}/{URL_INSPECTION_INSPECT_PATH}"

#: Statuses that mean the *request* was wrong rather than the URL. Each one
#: describes the property, the token, or the API, so it is identical for every
#: URL in the batch; repeating it once per page would bury the single useful
#: fact, which is that the run cannot work at all.
FATAL_STATUSES = frozenset({401, 403, 404})

#: Cap on an error message kept for a row. Enough to diagnose from, short
#: enough that one verbose Google error cannot bloat the CSV.
_MAX_ERROR_LENGTH = 200

#: ``requests.Session`` is not documented as thread safe, so each worker builds
#: and keeps its own. Reused across that worker's calls it still holds
#: connections open, which is the reason to use a session at all.
_thread_state = threading.local()


class UrlInspectionError(RuntimeError):
    """Raised when a URL Inspection run cannot produce usable data."""


class _RateLimiter:
    """Thread-safe pacer that spaces requests evenly across the quota.

    The slot is reserved under the lock and the wait happens outside it, so a
    worker that is waiting for its turn never blocks the others from reserving
    theirs. Spacing is even rather than bursty, which is what keeps a burst of
    parallel workers inside a per-minute quota.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self._interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        """Block until this caller's turn comes round."""
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = now if now > self._next_slot else self._next_slot
            self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


def _session() -> requests.Session:
    """Return this thread's retrying session, building it on first use."""
    session = getattr(_thread_state, "session", None)
    if session is None:
        session = build_retry_session()
        _thread_state.session = session
    return session


def _error_detail(response: requests.Response) -> str:
    """Return the most useful message Google gave for a failed request."""
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip()[:_MAX_ERROR_LENGTH]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        status = str(error.get("status") or error.get("code") or "")
        message = str(error.get("message") or "")
        if status and message:
            return f"{status}: {message}"[:_MAX_ERROR_LENGTH]
        return (status or message)[:_MAX_ERROR_LENGTH]
    return (response.text or "").strip()[:_MAX_ERROR_LENGTH]


def _failed_row(url: str, detail: str) -> dict[str, Any]:
    """Return a row recording why this one URL could not be inspected."""
    row = dict.fromkeys(URL_INSPECTION_COLUMNS, "")
    row["URL"] = url
    row["Error"] = detail
    return row


def _row_from_payload(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce one API response to a flat row of report columns.

    Every field is optional in practice: Google omits ``lastCrawlTime`` for a
    URL it has never fetched, and an error response carries none of the
    sub-results at all, so a missing value is an empty cell rather than a crash.
    """
    result = payload.get("inspectionResult") or {}
    index = result.get("indexStatusResult") or {}
    row = dict.fromkeys(URL_INSPECTION_COLUMNS, "")
    row.update(
        {
            "URL": url,
            "Verdict": index.get("verdict") or "",
            "Coverage state": index.get("coverageState") or "",
            "Robots.txt state": index.get("robotsTxtState") or "",
            "Indexing state": index.get("indexingState") or "",
            "Page fetch state": index.get("pageFetchState") or "",
            "Last crawl time": index.get("lastCrawlTime") or "",
            "Google canonical": index.get("googleCanonical") or "",
            "User canonical": index.get("userCanonical") or "",
            "Crawled as": index.get("crawledAs") or "",
            # The full list runs to hundreds of URLs and is mostly third-party
            # sites; the count is what shows internal link equity at a glance.
            "Referring URLs": str(len(index.get("referringUrls") or [])),
            "Google inspection link": result.get("inspectionResultLink") or "",
        }
    )
    return row


def _inspect_one(
    url: str, *, site_url: str, bearer_token: str, limiter: _RateLimiter
) -> dict[str, Any]:
    """Inspect one URL and return its row.

    Raises:
        UrlInspectionError: only for a batch-level failure, which would repeat
            identically for every remaining URL.
    """
    limiter.acquire()
    try:
        response = _session().post(
            INSPECT_URL,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"inspectionUrl": url, "siteUrl": site_url},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        # A network blip on one URL is that URL's problem, not the run's.
        return _failed_row(url, f"request failed: {error}")

    if response.status_code >= 400:
        detail = _error_detail(response)
        if response.status_code in FATAL_STATUSES:
            raise UrlInspectionError(
                f"URL Inspection rejected every request for {site_url} "
                f"(HTTP {response.status_code}): {detail}. Check that the URL "
                "Inspection API is enabled in the Google Cloud project, that "
                "GOOGLE_REFRESH_TOKEN grants the webmasters.readonly scope, and "
                f"that the authenticated account owns {site_url}."
            )
        return _failed_row(url, f"HTTP {response.status_code}: {detail}")

    try:
        payload = response.json()
    except ValueError:
        return _failed_row(url, "the response was not valid JSON")
    return _row_from_payload(url, payload)


def _select_targets(urls: Iterable[str], max_urls: int) -> list[str]:
    """Return the URLs to inspect: unique, http(s), and capped.

    De-duplication comes first so a site that links to itself several ways
    does not spend quota twice on one URL. Overflow is logged rather than
    silently dropped, because a short CSV that reads as complete is the failure
    mode worth avoiding.
    """
    unique = sorted({url.strip() for url in urls if url and url.strip()})
    selected = [url for url in unique if url.startswith(("http://", "https://"))]
    if max_urls > 0 and len(selected) > max_urls:
        logger.warning(
            "URL Inspection capped at %d of %d crawled URL(s); raise %s to "
            "inspect the rest",
            max_urls,
            len(selected),
            URL_INSPECTION_MAX_URLS_ENV_VAR,
        )
        return selected[:max_urls]
    return selected


def inspect_urls(
    *,
    site_url: str,
    urls: Sequence[str],
    concurrency: int = URL_INSPECTION_CONCURRENCY_DEFAULT,
    requests_per_minute: int = URL_INSPECTION_REQUESTS_PER_MINUTE_DEFAULT,
    max_urls: int = URL_INSPECTION_MAX_URLS_DEFAULT,
) -> list[dict[str, Any]]:
    """Inspect every URL in parallel and return one row per URL.

    Args:
        site_url: the Search Console property, e.g. ``sc-domain:diligentic.ca``.
        urls: the URLs to inspect, normally every page the crawl found.
        concurrency: how many URLs to inspect at once.
        requests_per_minute: the pacing ceiling, clamped to Google's documented
            per-minute quota so a misconfigured value cannot overrun it.
        max_urls: cap on how many URLs to inspect; ``0`` means no cap.

    Raises:
        UrlInspectionError: if the property, token, or API is unusable, or if
            nothing could be inspected at all.
    """
    targets = _select_targets(urls, max_urls)
    if not targets:
        raise UrlInspectionError(
            f"No HTTP URL to inspect for {site_url}: the crawl returned no pages."
        )

    # Clamp rather than trust. A rate above the documented quota is a typo, and
    # honouring it would only earn 429s.
    rate = max(1, min(requests_per_minute, URL_INSPECTION_QUOTA_PER_MINUTE))
    workers = max(1, min(concurrency, len(targets)))
    limiter = _RateLimiter(rate)
    bearer_token = get_access_token()

    logger.info(
        "Inspecting %d URL(s) against %s with %d worker(s) at <=%d request(s)/min",
        len(targets),
        site_url,
        workers,
        rate,
    )

    rows: list[dict[str, Any]] = []
    fatal: UrlInspectionError | None = None
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="url-inspect")
    try:
        futures: list[Future[dict[str, Any]]] = [
            pool.submit(
                _inspect_one,
                url,
                site_url=site_url,
                bearer_token=bearer_token,
                limiter=limiter,
            )
            for url in targets
        ]
        # ``as_completed`` yields in completion order, so the first fatal
        # failure reaches the loop as soon as it happens rather than after every
        # queued call has drained.
        completed: Iterable[Future[dict[str, Any]]] = as_completed(futures)
        for future in completed:
            try:
                rows.append(future.result())
            except UrlInspectionError as error:
                # Stop at once: every queued URL would fail the same way, so
                # draining the rest of the queue would only delay the error.
                fatal = error
                break
    finally:
        # A fatal failure must not wait for hundreds of queued calls to drain.
        pool.shutdown(wait=fatal is None, cancel_futures=fatal is not None)

    if fatal is not None:
        raise fatal
    if not rows:
        raise UrlInspectionError(
            f"No URL was inspected for {site_url}: the API returned no results."
        )

    rows.sort(key=lambda row: row.get("URL", ""))
    failed = sum(1 for row in rows if row.get("Error"))
    logger.info(
        "URL Inspection finished site=%s rows=%d failed=%d",
        site_url,
        len(rows),
        failed,
    )
    return rows


def validate_url_inspection_credentials() -> None:
    """Fail fast when the URL Inspection API cannot possibly work.

    The access token is fetched here so a missing or unscoped refresh token
    surfaces as a clear configuration error at the start of a run, rather than
    as hundreds of identical error rows in a CSV.
    """
    get_access_token()


def fetch_url_inspection_data(
    *,
    site_url: str,
    urls: Sequence[str],
    start_date: Any = None,
    end_date: Any = None,
) -> dict[str, Any]:
    """Return the URL Inspection report as serialisable rows.

    Args:
        site_url: the Search Console property to inspect against.
        urls: the URLs to inspect, normally the pages the crawl found.
        start_date: and ``end_date``: accepted so this function matches the
            audit stream interface. Indexing status is a point-in-time snapshot
            from Google, not a range, and carries no date range of its own.
    """
    del start_date, end_date
    if not env_bool(URL_INSPECTION_ENABLED_ENV_VAR, URL_INSPECTION_ENABLED_DEFAULT):
        logger.info(
            "%s is false; the URL Inspection report is not collected",
            URL_INSPECTION_ENABLED_ENV_VAR,
        )
        return {"rows": []}
    rows = inspect_urls(
        site_url=site_url,
        urls=urls,
        concurrency=env_int(
            URL_INSPECTION_CONCURRENCY_ENV_VAR, URL_INSPECTION_CONCURRENCY_DEFAULT
        ),
        requests_per_minute=env_int(
            URL_INSPECTION_REQUESTS_PER_MINUTE_ENV_VAR,
            URL_INSPECTION_REQUESTS_PER_MINUTE_DEFAULT,
        ),
        max_urls=env_int(
            URL_INSPECTION_MAX_URLS_ENV_VAR, URL_INSPECTION_MAX_URLS_DEFAULT
        ),
    )
    return {"rows": rows}
