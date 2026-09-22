from collections import defaultdict
from datetime import UTC, date, datetime
import re
from typing import Any

import requests

from constants.api_urls import (
    BING_PAGE_STATS_PATH,
    BING_QUERY_STATS_PATH,
    BING_WEBMASTER_API_BASE_URL,
)
from constants.bing import BING_API_KEY_ENV_VAR
from utils.get_env import _get_env
from utils.http import REQUEST_TIMEOUT_SECONDS, build_retry_session

_BING_DATE_PATTERN = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def _parse_bing_date(value: object) -> date | None:
    """Return a UTC date from Bing's ``/Date(milliseconds)/`` value."""
    if not isinstance(value, str):
        return None
    match = _BING_DATE_PATTERN.match(value)
    if match is None:
        return None
    try:
        return datetime.fromtimestamp(int(match.group(1)) / 1_000, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _number(value: object) -> float:
    """Coerce API metric values safely; malformed values count as zero."""
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalise_stats(
    stats: object,
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Filter daily Bing stats and aggregate them into the shared row format."""
    aggregates: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "clicks": 0.0,
            "impressions": 0.0,
            "position_total": 0.0,
            "position_weight": 0.0,
        }
    )

    if not isinstance(stats, list):
        return []

    for stat in stats:
        if not isinstance(stat, dict):
            continue
        stat_date = _parse_bing_date(stat.get("Date"))
        key = stat.get("Query")
        if (
            stat_date is None
            or not start_date <= stat_date <= end_date
            or not isinstance(key, str)
        ):
            continue

        clicks = _number(stat.get("Clicks"))
        impressions = _number(stat.get("Impressions"))
        position = _number(stat.get("AvgImpressionPosition"))
        aggregate = aggregates[key]
        aggregate["clicks"] += clicks
        aggregate["impressions"] += impressions
        if position >= 0 and impressions > 0:
            aggregate["position_total"] += position * impressions
            aggregate["position_weight"] += impressions

    rows: list[dict[str, Any]] = []
    for key, aggregate in aggregates.items():
        impressions = aggregate["impressions"]
        position_weight = aggregate["position_weight"]
        rows.append(
            {
                "keys": [key],
                "clicks": _display_number(aggregate["clicks"]),
                "impressions": _display_number(impressions),
                "ctr": aggregate["clicks"] / impressions if impressions else 0,
                "position": (
                    aggregate["position_total"] / position_weight
                    if position_weight
                    else ""
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -float(row["clicks"]),
            -float(row["impressions"]),
            row["keys"][0],
        ),
    )


def _display_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _fetch_stats(
    *,
    site_url: str,
    endpoint: str,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    api_key = _get_env(BING_API_KEY_ENV_VAR)
    http_session = session or build_retry_session()
    request_url = f"{BING_WEBMASTER_API_BASE_URL}/{endpoint}"
    try:
        response = http_session.get(
            request_url,
            params={"siteUrl": site_url, "apikey": api_key},
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        response_body = (
            error.response.text
            if isinstance(error, requests.RequestException) and error.response is not None
            else ""
        )
        date_range = f"{start_date.isoformat()}..{end_date.isoformat()}"
        raise RuntimeError(
            f"Bing {endpoint} request failed for {site_url} {date_range}: "
            f"{response_body or error}"
        ) from error
    finally:
        if session is None:
            http_session.close()

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Bing {endpoint} returned an unexpected response for {site_url}"
        )
    return {
        "rows": _normalise_stats(
            payload.get("d"), start_date=start_date, end_date=end_date
        )
    }


def validate_bing_credentials() -> None:
    _get_env(BING_API_KEY_ENV_VAR)


def fetch_query_data(
    *,
    site_url: str,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    return _fetch_stats(
        site_url=site_url,
        endpoint=BING_QUERY_STATS_PATH,
        start_date=start_date,
        end_date=end_date,
        session=session,
    )


def fetch_page_data(
    *,
    site_url: str,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    return _fetch_stats(
        site_url=site_url,
        endpoint=BING_PAGE_STATS_PATH,
        start_date=start_date,
        end_date=end_date,
        session=session,
    )
