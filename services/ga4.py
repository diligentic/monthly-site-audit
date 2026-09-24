from datetime import date
from typing import Any
from urllib.parse import quote

import requests

from constants.api_urls import (
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


def fetch_ga4_data(
    *,
    property_id_env_var: str,
    report: GA4Report,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    api_key = _get_env(GSC_API_KEY_ENV_VAR)
    bearer_token = get_access_token()
    property_id = _get_env(property_id_env_var)

    request_url = (
        f"{GOOGLE_ANALYTICS_DATA_BASE_URL}/"
        f"{GA4_RUN_REPORT_PATH.format(property_id=quote(property_id, safe=''))}"
    )
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    http_session = session or build_retry_session()
    try:
        response = http_session.post(
            request_url,
            params={"key": api_key},
            headers=headers,
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


def validate_ga4_credentials(sites: list[Site]) -> None:
    _get_env(GSC_API_KEY_ENV_VAR)
    validate_oauth_credentials()
    for site in sites:
        _get_env(site.ga4_property_id_env_var)
