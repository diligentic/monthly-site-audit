"""Scrapy settings for one crawl.

Every value is overridable through an environment variable (see
``constants/crawl.py``) so that a crawl can be tuned on the Render Free Tier
without a redeploy. The defaults encode the behaviour this audit needs:

* ``REDIRECT_ENABLED = False`` -- a redirect is a result to report, not a hop to
  follow blindly. The spider records the ``Location`` and requests an internal
  target itself, which keeps the original URL as the reported address.
* ``HTTPERROR_ALLOW_ALL = True`` -- 4xx and 5xx responses reach the spider
  callback and become rows. Without it Scrapy raises ``HttpError`` and a broken
  page would simply be missing from the report.
* ``ROBOTSTXT_OBEY = True`` -- the crawler identifies itself and obeys.
* ``METAREFRESH_ENABLED = False`` -- a meta refresh is a redirect in all but
  name, and following it automatically would hide it from the report.
* ``REQUEST_FINGERPRINTER_CLASS`` -- duplicate filtering runs on the normalised
  URL, so ``/about`` and ``/about/`` are fetched once.
* ``TELNETCONSOLE_ENABLED`` and ``REMOTE_CONTROL_ENABLED`` are off: both bind a
  local port that can execute crawler commands, which must not be reachable on
  a hosted service.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from constants.crawl import (
    CRAWL_AUTOTHROTTLE_DEFAULT,
    CRAWL_AUTOTHROTTLE_ENV_VAR,
    CRAWL_AUTOTHROTTLE_START_DELAY_DEFAULT,
    CRAWL_AUTOTHROTTLE_START_DELAY_ENV_VAR,
    CRAWL_AUTOTHROTTLE_TARGET_CONCURRENCY_DEFAULT,
    CRAWL_AUTOTHROTTLE_TARGET_CONCURRENCY_ENV_VAR,
    CRAWL_CONCURRENT_REQUESTS_DEFAULT,
    CRAWL_CONCURRENT_REQUESTS_ENV_VAR,
    CRAWL_DOWNLOAD_DELAY_DEFAULT,
    CRAWL_DOWNLOAD_DELAY_ENV_VAR,
    CRAWL_EXCLUDE_URL_PARAMS_DEFAULT,
    CRAWL_EXCLUDE_URL_PARAMS_ENV_VAR,
    CRAWL_EXTERNAL_HOST_LIMIT_DEFAULT,
    CRAWL_EXTERNAL_HOST_LIMIT_ENV_VAR,
    CRAWL_EXTERNAL_LINK_LIMIT_DEFAULT,
    CRAWL_EXTERNAL_LINK_LIMIT_ENV_VAR,
    CRAWL_LOG_LEVEL_DEFAULT,
    CRAWL_LOG_LEVEL_ENV_VAR,
    CRAWL_MAX_DEPTH_DEFAULT,
    CRAWL_MAX_DEPTH_ENV_VAR,
    CRAWL_MAX_URLS_DEFAULT,
    CRAWL_MAX_URLS_ENV_VAR,
    CRAWL_REQUEST_TIMEOUT_ENV_VAR,
    CRAWL_REQUEST_TIMEOUT_SECONDS_DEFAULT,
    CRAWL_RESPONSE_SIZE_LIMIT_MB_DEFAULT,
    CRAWL_RESPONSE_SIZE_LIMIT_MB_ENV_VAR,
    CRAWL_RETRY_TIMES_DEFAULT,
    CRAWL_RETRY_TIMES_ENV_VAR,
    CRAWL_ROBOTS_TXT_DEFAULT,
    CRAWL_ROBOTS_TXT_ENV_VAR,
    CRAWL_SITEMAP_DEFAULT,
    CRAWL_SITEMAP_ENV_VAR,
    CRAWL_TIMEOUT_ENV_VAR,
    CRAWL_TIMEOUT_SECONDS_DEFAULT,
    CRAWL_USER_AGENT_DEFAULT,
    CRAWL_USER_AGENT_ENV_VAR,
)
from constants.sitemap import SITEMAP_MAX_CHILDREN, SITEMAP_MAX_INDEX_DEPTH
from scrapy_crawl.errors import CrawlError

#: Settings that do not depend on the environment.
BASE_SETTINGS: dict[str, Any] = {
    "BOT_NAME": "mothly-site-audit",
    "SPIDER_MODULES": ["scrapy_crawl.spiders"],
    "NEWSPIDER_MODULE": "scrapy_crawl.spiders",
    "ROBOTSTXT_OBEY": True,
    "HTTPERROR_ALLOW_ALL": True,
    "REDIRECT_ENABLED": False,
    "METAREFRESH_ENABLED": False,
    "COOKIES_ENABLED": False,
    "TELNETCONSOLE_ENABLED": False,
    "REMOTE_CONTROL_ENABLED": False,
    "MEMDEBUG_ENABLED": False,
    "RETRY_ENABLED": True,
    # Scrapy's end-of-crawl stats dump is dozens of lines that land exactly
    # where the parent process looks for the reason a crawl came back short --
    # it would push the real errors out of the log tail the parent keeps. The
    # spider logs its own one-line summary in ``closed()`` instead, and Scrapy
    # still logs every request, warning, and error.
    "STATS_DUMP": False,
    "FEED_EXPORT_ENCODING": "utf-8",
    "REQUEST_FINGERPRINTER_CLASS": (
        "scrapy_crawl.fingerprint.NormalizedRequestFingerprinter"
    ),
}

#: Hard-kill overhead added to the in-spider wall-clock cap before the parent
#: process gives up on the crawl. It covers the reactor shutdown and the feed
#: flush, so a crawl that hit ``CLOSESPIDER_TIMEOUT`` still exits cleanly.
CRAWL_SHUTDOWN_GRACE_SECONDS = 120

#: Custom setting carrying the *page* budget to the spider. ``CRAWL_MAX_URLS``
#: names a number of pages and the spider is what enforces it, so the value
#: travels under its own name instead of being recovered from
#: ``CLOSESPIDER_PAGECOUNT``, which counts responses and is deliberately larger.
CRAWL_MAX_PAGES_SETTING = "CRAWL_MAX_PAGES"


def env_str(name: str, default: str = "") -> str:
    """Return a stripped environment value, or ``default`` when unset."""
    return (os.getenv(name) or "").strip() or default


def env_int(name: str, default: int, *, minimum: int | None = 0) -> int:
    """Return an integer environment value, validated against ``minimum``."""
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise CrawlError(f"{name} must be an integer, got {raw!r}") from error
    if minimum is not None and value < minimum:
        raise CrawlError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Return a float environment value, validated against ``minimum``."""
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise CrawlError(f"{name} must be a number, got {raw!r}") from error
    if value < minimum:
        raise CrawlError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_bool(name: str, default: bool) -> bool:
    """Return a boolean environment value (``true/false``, ``1/0``, ...)."""
    raw = env_str(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise CrawlError(f"{name} must be a boolean (true/false), got {raw!r}")


def env_list(name: str, default: tuple[str, ...]) -> list[str]:
    """Return a comma-separated environment value as a list."""
    raw = env_str(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_crawl_settings() -> dict[str, Any]:
    """Build the full Scrapy settings for one crawl from the environment."""
    concurrency = env_int(
        CRAWL_CONCURRENT_REQUESTS_ENV_VAR, CRAWL_CONCURRENT_REQUESTS_DEFAULT, minimum=1
    )
    max_urls = env_int(CRAWL_MAX_URLS_ENV_VAR, CRAWL_MAX_URLS_DEFAULT, minimum=1)
    return {
        **BASE_SETTINGS,
        "USER_AGENT": env_str(CRAWL_USER_AGENT_ENV_VAR, CRAWL_USER_AGENT_DEFAULT),
        "ROBOTSTXT_OBEY": env_bool(CRAWL_ROBOTS_TXT_ENV_VAR, CRAWL_ROBOTS_TXT_DEFAULT),
        "LOG_LEVEL": env_str(CRAWL_LOG_LEVEL_ENV_VAR, CRAWL_LOG_LEVEL_DEFAULT),
        "CONCURRENT_REQUESTS": concurrency,
        # Capped at the same value: these two small sites are a single host, so
        # a per-domain cap below the global one would only slow the crawl down.
        "CONCURRENT_REQUESTS_PER_DOMAIN": concurrency,
        "DOWNLOAD_DELAY": env_float(
            CRAWL_DOWNLOAD_DELAY_ENV_VAR, CRAWL_DOWNLOAD_DELAY_DEFAULT
        ),
        "DOWNLOAD_TIMEOUT": env_int(
            CRAWL_REQUEST_TIMEOUT_ENV_VAR, CRAWL_REQUEST_TIMEOUT_SECONDS_DEFAULT, minimum=1
        ),
        "RETRY_TIMES": env_int(
            CRAWL_RETRY_TIMES_ENV_VAR, CRAWL_RETRY_TIMES_DEFAULT, minimum=0
        ),
        "DOWNLOAD_MAXSIZE": int(
            env_float(
                CRAWL_RESPONSE_SIZE_LIMIT_MB_ENV_VAR,
                CRAWL_RESPONSE_SIZE_LIMIT_MB_DEFAULT,
                minimum=0.0,
            )
            * 1024
            * 1024
        ),
        "AUTOTHROTTLE_ENABLED": env_bool(
            CRAWL_AUTOTHROTTLE_ENV_VAR, CRAWL_AUTOTHROTTLE_DEFAULT
        ),
        "AUTOTHROTTLE_START_DELAY": env_float(
            CRAWL_AUTOTHROTTLE_START_DELAY_ENV_VAR,
            CRAWL_AUTOTHROTTLE_START_DELAY_DEFAULT,
        ),
        "AUTOTHROTTLE_MAX_DELAY": max(
            60.0,
            env_float(CRAWL_DOWNLOAD_DELAY_ENV_VAR, CRAWL_DOWNLOAD_DELAY_DEFAULT) * 4,
        ),
        "AUTOTHROTTLE_TARGET_CONCURRENCY": env_float(
            CRAWL_AUTOTHROTTLE_TARGET_CONCURRENCY_ENV_VAR,
            CRAWL_AUTOTHROTTLE_TARGET_CONCURRENCY_DEFAULT,
        ),
        "AUTOTHROTTLE_DEBUG": False,
        # Page budget. The spider counts pages itself and stops scheduling new
        # internal pages at the limit, so this is the number it enforces.
        CRAWL_MAX_PAGES_SETTING: max_urls,
        # The response-level ceiling behind it. ``CLOSESPIDER_PAGECOUNT`` counts
        # every response, not just pages, so it has to sit above the page budget
        # by the most non-page responses a crawl can legitimately fetch --
        # otherwise a site with external links or a nested sitemap would stop
        # several pages short of the budget that was asked for, and the page
        # count in the report would not match ``CRAWL_MAX_URLS``. This is a
        # ceiling for pathological input, not the budget.
        "CLOSESPIDER_PAGECOUNT": max_urls + non_page_response_headroom(),
        "CLOSESPIDER_TIMEOUT": env_int(
            CRAWL_TIMEOUT_ENV_VAR, CRAWL_TIMEOUT_SECONDS_DEFAULT, minimum=1
        ),
        # Scrapy reads a DEPTH_LIMIT of 0 as "no limit", so the minimum is 1:
        # the start URL is depth 0 and its own links are depth 1.
        "DEPTH_LIMIT": env_int(CRAWL_MAX_DEPTH_ENV_VAR, CRAWL_MAX_DEPTH_DEFAULT, minimum=1),
    }


def build_feed_settings(output_file: Path) -> dict[str, Any]:
    """Return the feed settings that stream the crawl records to ``output_file``."""
    return {
        "FEEDS": {
            str(output_file): {
                "format": "jsonlines",
                "encoding": "utf-8",
                "overwrite": True,
            }
        }
    }


def crawl_timeout_seconds() -> int:
    """Return the in-spider wall-clock cap for one crawl, in seconds."""
    return env_int(CRAWL_TIMEOUT_ENV_VAR, CRAWL_TIMEOUT_SECONDS_DEFAULT, minimum=1)


def non_page_response_headroom() -> int:
    """Return how many responses above the page budget a crawl may fetch.

    These are the responses that are not pages: the sitemap itself, the files a
    sitemap index points at, and the external link checks. They are already
    bounded by their own limits, and each of them costs a slot in Scrapy's
    response count, so the response ceiling has to leave room for all of them --
    or the crawl would stop short of ``CRAWL_MAX_URLS`` pages on any site that
    has external links, which is all of them.
    """
    link_limit, _host_limit = external_link_limits()
    return link_limit + SITEMAP_MAX_CHILDREN * SITEMAP_MAX_INDEX_DEPTH


def external_link_limits() -> tuple[int, int]:
    """Return the maximum distinct external URLs and hosts to check."""
    return (
        env_int(
            CRAWL_EXTERNAL_LINK_LIMIT_ENV_VAR,
            CRAWL_EXTERNAL_LINK_LIMIT_DEFAULT,
            minimum=0,
        ),
        env_int(
            CRAWL_EXTERNAL_HOST_LIMIT_ENV_VAR, CRAWL_EXTERNAL_HOST_LIMIT_DEFAULT, minimum=1
        ),
    )


def read_sitemap_enabled() -> bool:
    """Return whether the crawl should read the XML sitemaps."""
    return env_bool(CRAWL_SITEMAP_ENV_VAR, CRAWL_SITEMAP_DEFAULT)


def excluded_url_params() -> frozenset[str]:
    """Return query parameter names that do not identify a page."""
    return frozenset(
        item.lower()
        for item in env_list(
            CRAWL_EXCLUDE_URL_PARAMS_ENV_VAR, CRAWL_EXCLUDE_URL_PARAMS_DEFAULT
        )
    )
