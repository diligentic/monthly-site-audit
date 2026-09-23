import logging
import re
import struct
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.exceptions import RequestException

from constants.crawl import (
    CRAWL_TIMEOUT_SECONDS,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    MAX_RETRIES,
    MAX_URLS,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_STATUSES,
    USER_AGENT,
)

logger = logging.getLogger(__name__)
_CRAWL_CACHE: dict[str, dict[str, object]] = {}


def clear_crawl_cache() -> None:
    """Forget cached crawl snapshots so the next audit re-crawls the sites.

    The cache exists so the page and image streams share one crawl snapshot per
    run. A long-lived API process must not reuse a snapshot from a previous
    run, so ``run_audit`` clears it before collecting any data.
    """
    _CRAWL_CACHE.clear()


def normalize_url(url: str, base_url: str | None = None) -> str | None:
    absolute = urljoin(base_url or "", url.strip())
    absolute, _ = urldefrag(absolute)
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _same_site(url: str, root_host: str) -> bool:
    host = _host(url)
    return host == root_host or host.endswith("." + root_host)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []
        self.canonicals: list[str] = []
        self.robots: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag == "img" and values.get("src"):
            self.images.append(values["src"])
        elif tag == "source" and values.get("srcset"):
            candidate = values["srcset"].split(",", 1)[0].strip().split(" ", 1)[0]
            if candidate:
                self.images.append(candidate)
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            if values.get("href"):
                self.canonicals.append(values["href"])
        elif tag == "meta" and values.get("name", "").lower() in {
            "robots",
            "googlebot",
        }:
            self.robots.append(values.get("content", ""))


@dataclass
class _Fetched:
    response: requests.Response | None
    error: str = ""
    redirect_url: str = ""
    status_code: int | str = ""
    reason: str = ""


def _get(
    session: requests.Session, url: str, root_host: str, *, stream: bool = False
) -> _Fetched:
    current = url
    redirects: list[str] = []
    initial_status: int | str = ""
    initial_reason = ""
    for _ in range(MAX_REDIRECTS + 1):
        response = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = session.get(
                    current,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    stream=stream,
                    headers={"User-Agent": USER_AGENT},
                )
                if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                    response.close()
                    time.sleep(0.5 * (2**attempt))
                    continue
                break
            except RequestException as error:
                if attempt >= MAX_RETRIES:
                    return _Fetched(None, f"{type(error).__name__}: {error}")
                time.sleep(0.5 * (2**attempt))
        if response is None:
            return _Fetched(None, "request failed")
        if response.is_redirect or response.is_permanent_redirect:
            if not initial_status:
                initial_status, initial_reason = (
                    response.status_code,
                    response.reason or "",
                )
            location = response.headers.get("Location")
            response.close()
            if not location:
                return _Fetched(
                    None,
                    "redirect without Location",
                    status_code=initial_status,
                    reason=initial_reason,
                )
            destination = normalize_url(location, current)
            if not destination or not _same_site(destination, root_host):
                return _Fetched(
                    None,
                    "external redirect not followed",
                    destination or location,
                    initial_status,
                    initial_reason,
                )
            redirects.append(destination)
            current = destination
            continue
        return _Fetched(
            response,
            redirect_url=redirects[-1] if redirects else "",
            status_code=initial_status or response.status_code,
            reason=initial_reason or response.reason or "",
        )
    return _Fetched(None, "maximum redirects exceeded", current)


def _read_limited(
    response: requests.Response,
    limit: int,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue

            remaining = limit - size
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)

            if len(chunk) > remaining or size >= limit:
                return b"".join(chunks), True

    except (RequestException, OSError) as error:
        logger.warning(
            "Failed while reading response url=%s error=%s",
            response.url,
            error,
        )
        return b"".join(chunks), True

    return b"".join(chunks), False


def _dimension(data: bytes) -> tuple[int | str, int | str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:3] == b"GIF" and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"\xff\xd8":
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            pos += 2
            if marker in {0xD8, 0xD9}:
                continue
            if pos + 2 > len(data):
                break
            length = int.from_bytes(data[pos : pos + 2], "big")
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and pos + 7 <= len(data):
                return int.from_bytes(data[pos + 5 : pos + 7], "big"), int.from_bytes(
                    data[pos + 3 : pos + 5], "big"
                )
            pos += max(length, 2)
    return "", ""


def _robots(
    session: requests.Session, root: str, root_host: str
) -> tuple[RobotFileParser, str, list[str]]:
    url = urljoin(root, "/robots.txt")
    fetched = _get(session, url, root_host)
    parser = RobotFileParser(url)
    if fetched.response is None:
        status, lines = "unreachable", []
    elif fetched.response.status_code == 404:
        status, lines = "missing", []
        fetched.response.close()
    elif fetched.response.status_code >= 400:
        status, lines = "unreachable", []
        fetched.response.close()
    else:
        status = "available"
        lines = fetched.response.text.splitlines()
        fetched.response.close()
    sitemaps = [
        line.split(":", 1)[1].strip()
        for line in lines
        if line.lower().startswith("sitemap:")
    ]
    parser.parse(lines)
    logger.info("robots.txt %s: %s", status, url)
    return parser, status, sitemaps


def _sitemap_urls(
    session: requests.Session, url: str, root_host: str, seen: set[str], depth: int = 0
) -> list[str]:
    if depth > 5 or url in seen or not _same_site(url, root_host):
        return []
    seen.add(url)
    fetched = _get(session, url, root_host)
    if fetched.response is None or fetched.response.status_code >= 400:
        if fetched.response:
            fetched.response.close()
        return []
    body, _ = _read_limited(fetched.response, MAX_RESPONSE_BYTES)
    fetched.response.close()
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    tag = root.tag.rsplit("}", 1)[-1]
    key = "sitemap" if tag == "sitemapindex" else "url"
    found: list[str] = []
    for entry in root:
        if len(found) >= MAX_URLS:
            break
        if entry.tag.rsplit("}", 1)[-1] != key:
            continue
        loc = next(
            (child.text for child in entry if child.tag.rsplit("}", 1)[-1] == "loc"),
            None,
        )
        normalized = normalize_url(loc or "", url)
        if not normalized or not _same_site(normalized, root_host):
            continue
        if tag == "sitemapindex":
            found.extend(
                _sitemap_urls(session, normalized, root_host, seen, depth + 1)[
                    : MAX_URLS - len(found)
                ]
            )
        else:
            found.append(normalized)
    return found


def _noindex(directives: list[str]) -> bool:
    return any(
        "noindex" in {item.lower().strip() for item in re.split(r"[,\s]+", value)}
        for value in directives
    )


def _image_row(
    session: requests.Session, url: str, root_host: str
) -> dict[str, object]:
    fetched = _get(session, url, root_host, stream=True)
    response = fetched.response
    row: dict[str, object] = {
        "image_url": url,
        "status": response.status_code if response else "",
        "content_type": response.headers.get("Content-Type", "") if response else "",
        "file_size": "",
        "width": "",
        "height": "",
        "error": fetched.error,
    }
    if response:
        data, truncated = _read_limited(response, MAX_IMAGE_BYTES)
        content_length = response.headers.get("Content-Length", "")
        row["file_size"] = content_length or ("" if truncated else str(len(data)))
        row["width"], row["height"] = _dimension(data)
        if not row["width"]:
            row["error"] = (
                "image size limit reached"
                if truncated
                else "dimensions unavailable for format"
            )
        response.close()
    return row


def fetch_crawl_data(
    *, site_url: str, start_date=None, end_date=None, session=None
) -> dict[str, object]:
    """Fetch bounded internal page and image data; dates are accepted for audit compatibility."""
    del start_date, end_date, session
    if site_url in _CRAWL_CACHE:
        return _CRAWL_CACHE[site_url]
    start = normalize_url(site_url)
    if not start:
        raise ValueError(f"Invalid crawl start URL: {site_url!r}")
    parts = urlsplit(start)
    root_host = _host(start)
    root = f"{parts.scheme}://{parts.netloc}/"
    started = time.monotonic()
    http = requests.Session()
    http.headers.update({"User-Agent": USER_AGENT})
    logger.info(
        "Crawl started target=%s max_urls=%d timeout=%ds",
        start,
        MAX_URLS,
        CRAWL_TIMEOUT_SECONDS,
    )
    robots, robots_status, declared = _robots(http, root, root_host)
    sitemap_urls = [
        normalize_url(u) for u in [*declared, urljoin(root, "/sitemap.xml")]
    ]
    queue: deque[str] = deque([start])
    scheduled = {start}
    sitemap_seen: set[str] = set()
    for sitemap in sitemap_urls:
        if sitemap:
            found = _sitemap_urls(http, sitemap, root_host, sitemap_seen)
            queue.extend(url for url in found if url not in scheduled)
            scheduled.update(found)
            if found:
                logger.info("Sitemap %s discovered %d URLs", sitemap, len(found))

    visited: set[str] = set()
    rows: list[dict[str, object]] = []
    image_urls: set[str] = set()
    inlinks: dict[str, set[str]] = {}
    next_request_at = 0.0
    truncated = False
    while queue:
        if (
            time.monotonic() - started >= CRAWL_TIMEOUT_SECONDS
            or len(visited) >= MAX_URLS
        ):
            truncated = True
            logger.warning(
                "Crawl limit reached: visited=%d queued=%d", len(visited), len(queue)
            )
            break
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        status: int | str = ""
        final_status: int | str = ""
        reason = (
            "Robots disallowed"
            if robots_status == "available" and not robots.can_fetch(USER_AGENT, url)
            else ""
        )
        content_type = ""
        final_url = url
        canonical = ""
        redirect_url = ""
        indexability, index_status = "Non-Indexable", "HTTP error"
        if reason:
            index_status = "Robots disallowed"
        links: list[str] = []
        image_sources: list[str] = []
        if not reason:
            pause = next_request_at - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            next_request_at = time.monotonic() + REQUEST_DELAY_SECONDS
            fetched = _get(http, url, root_host)
            response = fetched.response
            redirect_url = fetched.redirect_url
            if response is None:
                reason = fetched.error
                if (
                    isinstance(fetched.status_code, int)
                    and 300 <= fetched.status_code < 400
                ):
                    status, reason, index_status = (
                        fetched.status_code,
                        fetched.reason,
                        "Redirect",
                    )
                else:
                    index_status = "Request failure"
            else:
                status = fetched.status_code or response.status_code
                final_status = response.status_code
                reason = fetched.reason or response.reason or ""
                final_url = normalize_url(response.url) or response.url
                content_type = response.headers.get("Content-Type", "")
                redirected = bool(fetched.redirect_url) or (
                    isinstance(status, int) and 300 <= status < 400
                )
                if redirected:
                    index_status = "Redirect"
                elif response.status_code >= 400:
                    index_status = "HTTP error"
                elif (
                    "text/html" in content_type.lower()
                    or "application/xhtml+xml" in content_type.lower()
                ):
                    data, too_large = _read_limited(response, MAX_RESPONSE_BYTES)
                    parser = _PageParser()
                    parser.feed(
                        data.decode(response.encoding or "utf-8", errors="replace")
                    )
                    links, image_sources = parser.links, parser.images
                    canonical = (
                        normalize_url(parser.canonicals[0], final_url)
                        if parser.canonicals
                        else ""
                    )
                    noindex = _noindex(parser.robots)
                    header_noindex = _noindex(
                        [response.headers.get("X-Robots-Tag", "")]
                    )
                    if redirected:
                        indexability, index_status = "Non-Indexable", "Redirect"
                    elif noindex or header_noindex:
                        indexability = "Non-Indexable"
                        index_status = (
                            "X-Robots-Tag: noindex"
                            if header_noindex
                            else "Noindex directive"
                        )
                    else:
                        indexability, index_status = "Indexable", "Indexable"
                    if too_large:
                        reason = "HTML truncated at response size limit"
                else:
                    index_status = "Non-HTML resource"
                response.close()
        for href in links:
            target = normalize_url(href, final_url)
            if target and _same_site(target, root_host):
                inlinks.setdefault(target, set()).add(url)
                if target not in scheduled and len(scheduled) < MAX_URLS:
                    scheduled.add(target)
                    queue.append(target)
        for source in image_sources:
            image_url = normalize_url(source, final_url)
            if (
                image_url
                and _same_site(image_url, root_host)
                and len(image_urls) < MAX_IMAGES
            ):
                image_urls.add(image_url)
        rows.append(
            {
                "url": url,
                "final_url": final_url,
                "status": status,
                "reason": reason,
                "final_status": final_status,
                "content_type": content_type,
                "indexability": indexability,
                "indexability_status": index_status,
                "canonical": canonical,
                "unique_inlinks": 0,
                "redirect_url": redirect_url,
            }
        )
        logger.info("Crawled %d %s status=%s", len(visited), url, status or reason)

    for row in rows:
        row["unique_inlinks"] = len(inlinks.get(str(row["url"]), set()))
        row["crawl_truncated"] = truncated
    image_rows: list[dict[str, object]] = []
    for image_url in sorted(image_urls):
        if time.monotonic() - started >= CRAWL_TIMEOUT_SECONDS:
            image_rows.append(
                {
                    "image_url": image_url,
                    "status": "",
                    "content_type": "",
                    "file_size": "",
                    "width": "",
                    "height": "",
                    "error": "crawl timeout",
                }
            )
            continue
        pause = next_request_at - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        next_request_at = time.monotonic() + REQUEST_DELAY_SECONDS
        image_rows.append(_image_row(http, image_url, root_host))
    http.close()
    logger.info(
        "Crawl complete pages=%d images=%d urls_discovered=%d robots=%s truncated=%s",
        len(rows),
        len(image_rows),
        len(scheduled),
        robots_status,
        truncated,
    )
    result: dict[str, object] = {
        "rows": rows,
        "image_rows": image_rows,
        "truncated": truncated,
    }
    _CRAWL_CACHE[site_url] = result
    return result
