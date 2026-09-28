"""Errors raised by the crawl module."""

from __future__ import annotations


class CrawlError(RuntimeError):
    """Raised when a crawl cannot be started, completed, or read.

    The message is always actionable on its own: it names the site, the output
    file, and the most likely cause, because a crawl failure is otherwise
    invisible from the outside -- Scrapy closes the spider on an empty queue
    and a report built from an empty file looks like a healthy site.
    """
