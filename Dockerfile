FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --silent --show-error \
        https://download.screamingfrog.co.uk/products/seo-spider/screamingfrogseospider_24.3_amd64.deb \
        --output /tmp/screamingfrogseospider.deb \
    && apt-get update \
    && apt-get install --yes --no-install-recommends /tmp/screamingfrogseospider.deb \
    && rm -f /tmp/screamingfrogseospider.deb \
    && rm -rf /var/lib/apt/lists/* \
    && command -v screamingfrogseospider

COPY pyproject.toml uv.lock README.md ./
COPY app.py main.py ./
COPY constants ./constants
COPY services ./services
COPY utils ./utils

RUN uv sync --frozen --no-dev

EXPOSE 10000
CMD ["sh", "-c", "uv run --no-sync fastapi run app.py --host 0.0.0.0 --port ${PORT:-10000}"]
