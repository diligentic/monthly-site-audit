from datetime import date
from typing import Any
from urllib.parse import quote

import requests

from constants.api_urls import (
    GOOGLE_SEARCH_CONSOLE_API_BASE_URL,
    SEARCH_ANALYTICS_QUERY_PATH,
)
from constants.search_console import (
    GSC_API_KEY_ENV_VAR,
    GSC_BEARER_TOKEN_ENV_VAR,
    SEARCH_ANALYTICS_DIMENSIONS,
    SEARCH_ANALYTICS_PAGE_DIMENSIONS,
    SEARCH_ANALYTICS_ROW_LIMIT,
)
from utils.get_env import _get_env
from utils.http import REQUEST_TIMEOUT_SECONDS, build_retry_session


def _country_filter_group(country: str) -> dict[str, Any]:
    return {
        "groupType": "and",
        "filters": [
            {
                "dimension": "country",
                "operator": "equals",
                "expression": country,
            }
        ],
    }


def _build_query_payload(
    start_date: date,
    end_date: date,
    dimensions: list[str],
    *,
    country: str | None = None,
) -> dict[str, Any]:
    payload = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": dimensions,
        "rowLimit": SEARCH_ANALYTICS_ROW_LIMIT,
    }
    if country:
        payload["dimensionFilterGroups"] = [_country_filter_group(country)]
    return payload


def _fetch_metrics(
    *,
    site_url: str,
    start_date: date,
    end_date: date,
    dimensions: list[str],
    country: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    api_key = _get_env(GSC_API_KEY_ENV_VAR)
    bearer_token = _get_env(GSC_BEARER_TOKEN_ENV_VAR)

    request_payload = _build_query_payload(
        start_date, end_date, dimensions, country=country
    )
    encoded_site_url = quote(site_url, safe="")
    request_url = (
        f"{GOOGLE_SEARCH_CONSOLE_API_BASE_URL}/"
        f"{SEARCH_ANALYTICS_QUERY_PATH.format(site_url=encoded_site_url)}"
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
            json=request_payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        response_body = error.response.text if error.response is not None else ""
        date_range = f"{start_date.isoformat()}..{end_date.isoformat()}"
        dimension_label = "+".join(dimensions)
        raise RuntimeError(
            f"Search Console request failed for {site_url} {date_range} "
            f"(dimensions={dimension_label}): {response_body or error}"
        ) from error
    finally:
        if session is None:
            http_session.close()


def validate_credentials() -> None:
    _get_env(GSC_API_KEY_ENV_VAR)
    _get_env(GSC_BEARER_TOKEN_ENV_VAR)


def fetch_query_data(
    *,
    site_url: str,
    start_date: date,
    end_date: date,
    country: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    return _fetch_metrics(
        site_url=site_url,
        start_date=start_date,
        end_date=end_date,
        dimensions=SEARCH_ANALYTICS_DIMENSIONS,
        country=country,
        session=session,
    )


def fetch_page_data(
    *,
    site_url: str,
    start_date: date,
    end_date: date,
    country: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    return _fetch_metrics(
        site_url=site_url,
        start_date=start_date,
        end_date=end_date,
        dimensions=SEARCH_ANALYTICS_PAGE_DIMENSIONS,
        country=country,
        session=session,
    )
