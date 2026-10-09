from datetime import date
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import requests

from constants.api_urls import (
    GA4_METADATA_PATH,
    GA4_RUN_REPORT_PATH,
    GOOGLE_ANALYTICS_DATA_BASE_URL,
)
from constants.ga4 import GA4Report
from constants.search_console import GSC_API_KEY_ENV_VAR
from constants.sites import Site
from services.oauth import get_access_token, validate_oauth_credentials
from utils.get_env import _get_env
from utils.http import REQUEST_TIMEOUT_SECONDS, build_retry_session


def _build_ga4_payload(
    report: GA4Report,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    payload = {
        "dateRanges": [
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            }
        ],
        "dimensions": report.dimensions,
        "metrics": report.metrics,
    }
    payload.update(report.extra_payload)
    return payload


def _ga4_request_url(property_id: str, path_template: str) -> str:
    return (
        f"{GOOGLE_ANALYTICS_DATA_BASE_URL}/"
        f"{path_template.format(property_id=quote(property_id, safe=''))}"
    )


def _ga4_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def fetch_ga4_data(
    *,
    property_id_env_var: str,
    report: GA4Report,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    api_key = _get_env(GSC_API_KEY_ENV_VAR)
    property_id = _get_env(property_id_env_var)
    request_url = _ga4_request_url(property_id, GA4_RUN_REPORT_PATH)

    http_session = session or build_retry_session()
    try:
        response = http_session.post(
            request_url,
            params={"key": api_key},
            headers=_ga4_headers(),
            json=_build_ga4_payload(report, start_date, end_date),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        response_body = error.response.text if error.response is not None else ""
        date_range = f"{start_date.isoformat()}..{end_date.isoformat()}"
        raise RuntimeError(
            f"GA4 {report.label} request failed for property {property_id} "
            f"{date_range}: {response_body or error}"
        ) from error
    finally:
        if session is None:
            http_session.close()


def _fetch_ga4_dimension_names(
    *,
    property_id_env_var: str,
    session: requests.Session | None = None,
) -> frozenset[str]:
    """Return the API names of every dimension exposed by a GA4 property.

    GA4 properties only publish custom event dimensions (for example
    ``customEvent:link_path``) once they have been registered and have seen
    traffic, so a property may legitimately not report them yet.
    """
    api_key = _get_env(GSC_API_KEY_ENV_VAR)
    property_id = _get_env(property_id_env_var)
    request_url = _ga4_request_url(property_id, GA4_METADATA_PATH)

    http_session = session or build_retry_session()
    try:
        response = http_session.get(
            request_url,
            params={"key": api_key},
            headers=_ga4_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        metadata = response.json()
    except requests.RequestException as error:
        response_body = error.response.text if error.response is not None else ""
        raise RuntimeError(
            f"GA4 metadata request failed for property {property_id}: "
            f"{response_body or error}"
        ) from error
    finally:
        if session is None:
            http_session.close()

    return frozenset(
        dimension["apiName"]
        for dimension in metadata.get("dimensions") or []
        if dimension.get("apiName")
    )


@lru_cache(maxsize=None)
def _cached_ga4_dimension_names(property_id_env_var: str) -> frozenset[str]:
    return _fetch_ga4_dimension_names(property_id_env_var=property_id_env_var)


def ga4_dimension_names(property_id_env_var: str) -> frozenset[str]:
    """Return the property's dimension names, cached for the current run."""
    return _cached_ga4_dimension_names(property_id_env_var)


def clear_ga4_metadata_cache() -> None:
    """Forget cached property metadata so each run sees the latest schema.

    A long-lived API process would otherwise keep returning the dimensions
    discovered during the first run, which would hide custom event dimensions
    that were added afterwards.
    """
    _cached_ga4_dimension_names.cache_clear()


def validate_ga4_credentials(sites: list[Site]) -> None:
    _get_env(GSC_API_KEY_ENV_VAR)
    validate_oauth_credentials()
    for site in sites:
        _get_env(site.ga4_property_id_env_var)
