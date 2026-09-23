from datetime import date
from typing import Any

import requests

from constants.api_urls import PAGESPEED_API_BASE_URL, PAGESPEED_RUN_PATH
from constants.search_console import GSC_API_KEY_ENV_VAR
from utils.get_env import _get_env
from utils.http import REQUEST_TIMEOUT_SECONDS, build_retry_session

_STRATEGIES = ("mobile", "desktop")
_AUDIT_METRICS = {
    "lcp_ms": "largest-contentful-paint",
    "inp_ms": "interaction-to-next-paint",
    "cls": "cumulative-layout-shift",
}
_METRICS_AUDIT_KEYS = {
    "lcp_ms": "largestContentfulPaint",
    "inp_ms": "interactionToNextPaint",
    "cls": "cumulativeLayoutShift",
}


def _metric_value(value: object) -> int | float | str:
    """Return a CSV-safe Lighthouse numeric value, or blank if unavailable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _lighthouse_metrics(payload: object) -> dict[str, int | float | str]:
    """Extract lab metrics, accommodating Lighthouse's metrics-audit variant."""
    empty_metrics = {name: "" for name in _AUDIT_METRICS}
    if not isinstance(payload, dict):
        return empty_metrics

    lighthouse_result = payload.get("lighthouseResult")
    if not isinstance(lighthouse_result, dict):
        return empty_metrics
    audits = lighthouse_result.get("audits")
    if not isinstance(audits, dict):
        return empty_metrics

    metrics_audit = audits.get("metrics")
    metrics_item: dict[str, Any] = {}
    if isinstance(metrics_audit, dict):
        details = metrics_audit.get("details")
        items = details.get("items") if isinstance(details, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            metrics_item = items[0]

    result: dict[str, int | float | str] = {}
    for column, audit_id in _AUDIT_METRICS.items():
        audit = audits.get(audit_id)
        audit_value = audit.get("numericValue") if isinstance(audit, dict) else None
        value = audit_value if audit_value is not None else metrics_item.get(
            _METRICS_AUDIT_KEYS[column]
        )
        result[column] = _metric_value(value)
    return result


def _fetch_strategy(
    *,
    site_url: str,
    strategy: str,
    api_key: str,
    session: requests.Session,
) -> dict[str, int | float | str]:
    request_url = f"{PAGESPEED_API_BASE_URL}/{PAGESPEED_RUN_PATH}"
    try:
        response = session.get(
            request_url,
            params={
                "url": site_url,
                "key": api_key,
                "strategy": strategy,
                "category": "PERFORMANCE",
            },
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
        raise RuntimeError(
            f"PageSpeed {strategy} request failed for {site_url}: "
            f"{response_body or error}"
        ) from error

    if not isinstance(payload, dict) or not isinstance(
        payload.get("lighthouseResult"), dict
    ):
        raise RuntimeError(
            f"PageSpeed {strategy} returned an unexpected response for {site_url}"
        )
    return {"device": strategy, **_lighthouse_metrics(payload)}


def fetch_web_core_vitals_data(
    *,
    site_url: str,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Collect mobile and desktop PageSpeed lab metrics for one site.

    ``start_date`` and ``end_date`` are accepted to satisfy the common stream
    interface. PageSpeed is a point-in-time measurement, so they do not alter
    the request.
    """
    del start_date, end_date
    api_key = _get_env(GSC_API_KEY_ENV_VAR)
    http_session = session or build_retry_session()
    try:
        rows = [
            _fetch_strategy(
                site_url=site_url,
                strategy=strategy,
                api_key=api_key,
                session=http_session,
            )
            for strategy in _STRATEGIES
        ]
    finally:
        if session is None:
            http_session.close()
    return {"rows": rows}
