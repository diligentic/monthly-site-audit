from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import requests

from constants.sitemap import SITEMAP_MAX_INDEX_DEPTH, SITEMAP_NAMESPACE
from utils.http import REQUEST_TIMEOUT_SECONDS, build_retry_session


def _tag(name: str) -> str:
    return f"{{{SITEMAP_NAMESPACE}}}{name}"


def _find_text(parent: ElementTree.Element, name: str) -> str | None:
    element = parent.find(_tag(name))
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _normalise_lastmod(value: str | None) -> str:
    if not value:
        return ""
    candidate = value.split("T", 1)[0]
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return value
    return candidate


def _parse_urlset(root: ElementTree.Element) -> list[dict[str, Any]]:
    rows = []
    for url_el in root:
        if url_el.tag != _tag("url"):
            continue
        location = _find_text(url_el, "loc")
        if not location:
            continue
        rows.append(
            {
                "url": location,
                "lastmod": _normalise_lastmod(_find_text(url_el, "lastmod")),
                "changefreq": _find_text(url_el, "changefreq") or "",
                "priority": _find_text(url_el, "priority") or "",
            }
        )
    return rows


def _child_sitemap_locations(root: ElementTree.Element) -> list[str]:
    locations = []
    for sitemap_el in root:
        if sitemap_el.tag != _tag("sitemap"):
            continue
        location = _find_text(sitemap_el, "loc")
        if location:
            locations.append(location)
    return locations


def _fetch_xml(url: str, session: requests.Session) -> ElementTree.Element:
    try:
        response = session.get(
            url,
            headers={"Accept": "application/xml, text/xml, */*"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return ElementTree.fromstring(response.content)
    except requests.RequestException as error:
        response_body = error.response.text if error.response is not None else ""
        raise RuntimeError(
            f"Sitemap request failed for {url}: {response_body or error}"
        ) from error
    except ElementTree.ParseError as error:
        raise RuntimeError(f"Sitemap at {url} is not valid XML: {error}") from error


def _walk_sitemap(
    sitemap_url: str,
    *,
    depth: int,
    session: requests.Session,
    visited: set[str],
    rows: list[dict[str, Any]],
) -> None:
    if depth > SITEMAP_MAX_INDEX_DEPTH or sitemap_url in visited:
        return
    visited.add(sitemap_url)

    root = _fetch_xml(sitemap_url, session)
    if root.tag == _tag("urlset"):
        rows.extend(_parse_urlset(root))
        return
    if root.tag == _tag("sitemapindex"):
        for child_url in _child_sitemap_locations(root):
            _walk_sitemap(
                child_url,
                depth=depth + 1,
                session=session,
                visited=visited,
                rows=rows,
            )
        return
    raise RuntimeError(f"Unsupported sitemap root <{root.tag}> at {sitemap_url}")


def fetch_sitemap_data(
    *,
    sitemap_url: str,
    start_date: date,
    end_date: date,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    http_session = session or build_retry_session()
    try:
        rows: list[dict[str, Any]] = []
        _walk_sitemap(
            sitemap_url,
            depth=0,
            session=http_session,
            visited=set(),
            rows=rows,
        )
    finally:
        if session is None:
            http_session.close()

    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["url"], row)
    return {"rows": sorted(unique.values(), key=lambda row: row["url"])}
