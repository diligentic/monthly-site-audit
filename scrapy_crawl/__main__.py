"""Allow ``python -m scrapy_crawl`` to run the crawl command line interface."""

from __future__ import annotations

import sys

from scrapy_crawl.cli import main

if __name__ == "__main__":
    sys.exit(main())
