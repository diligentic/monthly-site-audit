#!/usr/bin/env bash
set -euo pipefail

# The crawler is advertools/Scrapy, a pure-Python dependency, so the build only
# has to install the project dependencies. No Java runtime, no external binary.
uv sync --frozen
