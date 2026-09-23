# Performance audit (Search Console + Bing + GA4)

Collects performance data from Google Search Console and Bing Webmaster
(queries, pages), Google Analytics 4 (traffic by source/medium), sitemap and
Core Web Vitals snapshots, and an HTTP based internal page and image crawl.
It stores one CSV file per site, month, and data stream.

Set these values in `.env` before running the program:

```env
GSC_API_KEY=your-google-api-key
GSC_BEARER_TOKEN=your-oauth-2-access-token
BING_API_KEY=your-bing-webmaster-api-key
GA4_PROPERTY_ID_DILIGENTIC=your-diligentic-ga4-property-id
GA4_PROPERTY_ID_AJAYKUMAR=your-ajaykumar-ga4-property-id
```

GA4 reuses the Search Console API key and OAuth bearer token. Bing uses its
own API key, shared by both sites. Sitemap data needs no credentials: it is
fetched from the site's public `sitemap.xml` endpoint. Core Web Vitals uses the
same Google API key as Search Console.

The built-in Python crawler does not use Screaming Frog, Wget, a commercial
license, or a browser. On Render/Linux, attach a persistent disk and point
`DATA_ROOT` (all CSV output) and `CRAWL_STORAGE_ROOT` (crawl/image files) at its
mount path — for example `/var/data`. Crawl files are stored under each site's
`Crawls/` and `Images/` folders. The crawler honors `robots.txt` when available;
if it is missing or unreachable, it continues with same-site URL restrictions
and a half-second request delay. `MAX_URLS`, request timeout, crawl timeout,
redirect, retry, and response-size limits are defined in `constants/crawl.py`.

Run the audit locally with:

```bash
uv run python main.py
```

The CLI and the HTTP API below share the exact same orchestration
(`services/audit_runner.py`), so results are identical either way.

## HTTP API

The collection also runs on demand through a small FastAPI service (`app.py`).
Nothing is fetched at startup or import time — data is only collected when a run
is triggered. The GitHub Actions workflow posts to this API to start a run,
which keeps data collection off the CI runner and on the server with the
persistent disk.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe (no auth). |
| `POST` | `/api/v1/audit/runs` | Start an audit in the background. Returns `202` with the `run_id`, or `409` if a run is already in progress. |

When `AUDIT_API_KEY` is set in the environment, requests must send it as the
`X-Api-Key` header (except `/healthz`). Start a run:

```bash
curl -X POST http://localhost:8000/api/v1/audit/runs \
  -H "X-Api-Key: ${AUDIT_API_KEY}" -H "Content-Type: application/json" \
  -d '{}'
# {"run_id":"...","status":"running"}
```

The optional JSON body accepts `site` (a site name) and `date` (an anchor date,
useful for testing). A run with an empty body audits every scheduled site; the
quarterly site is skipped automatically outside its quarter months.

### Deployment (Render)

Native web service (not Docker), **Runtime: Python**:

- **Build command** (installs uv, then installs dependencies):
  ```bash
  uv sync 
  ```
- **Start command** (single worker — run state lives in memory):
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  uv run uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1
  ```

Attach a **persistent disk** (Render → Disks) and point the storage env vars at
its mount path, e.g. mount the disk at `/var/data` and set on the service:

```env
DATA_ROOT=/var/data
CRAWL_STORAGE_ROOT=/var/data
AUDIT_API_KEY=<same value used by the GitHub Actions secret>
```

`DATA_ROOT` is where every provider writes its CSVs; `CRAWL_STORAGE_ROOT` is
used for the crawl/image files.

### GitHub Actions

The workflow in `.github/workflows/monthly-audit.yml` starts a run on the 5th
of each month (and on demand via `workflow_dispatch`). It validates that both
secrets are set, posts to `POST /api/v1/audit/runs`, and fails the job if the
API does not return `202`. Set two secrets on the repository:

- `AUDIT_API_URL` — the deployed API base URL (no trailing slash).
- `AUDIT_API_KEY` — the same value as `AUDIT_API_KEY` on the server.

A run triggered twice while the first is still running returns `409`.
Failures are written to the application logs and visible as missing or partial
CSVs on the persistent disk; the audit does not block on them — one failing
stream or month does not stop the rest of the run.

## Sites

| Site | Search Console property | GA4 property | Schedule | Window | Always fetch | Folder |
|---|---|---|---|---|---|---|
| Diligentic | `sc-domain:diligentic.ca` | env `GA4_PROPERTY_ID_DILIGENTIC` | Monthly | previous 2 months | newest 1 month | `data/Diligentic/` |
| AjayKumar | `sc-domain:ajaykumar.ca` | env `GA4_PROPERTY_ID_AJAYKUMAR` | Quarterly (Jan, Apr, Jul, Oct) | previous 6 months | newest 3 months | `data/AjayKumar/` |

Within a site's window the **always-fetch** months (the newest data) are
refetched on every run, while the older months are fetched only when their CSV
file is missing. The first run therefore backfills the whole window; later runs
only fetch what is new since the previous run.

Data is stored by site and data source (provider), so adding another provider
only adds a new provider folder:

```text
data/
├── Diligentic/
│   ├── GSC/
│   │   ├── queries_2026-08.csv
│   │   ├── pages_2026-08.csv
│   │   ├── canada_queries_2026-08.csv
│   │   └── canada_pages_2026-08.csv
│   ├── Bing/
│   │   ├── queries_2026-08.csv
│   │   └── pages_2026-08.csv
│   ├── GA4/
│   │   ├── traffic_acquisition_2026-08.csv
│   │   ├── landing_2026-08.csv
│   │   └── events_2026-08.csv
│   ├── Sitemap/
│       └── sitemap_2026-08.csv
│   ├── Crawls/
│       └── 2026-08.csv
│   ├── Images/
│       └── images_2026-08.csv
│   └── WebCoreVitals/
│       └── web_core_vitals_2026-08.csv
└── AjayKumar/
    ├── GSC/
    │   ├── queries_2026-07.csv
    │   ├── pages_2026-07.csv
    │   ├── canada_queries_2026-07.csv
    │   └── canada_pages_2026-07.csv
    ├── Bing/
    │   ├── queries_2026-07.csv
    │   └── pages_2026-07.csv
    ├── GA4/
    │   ├── traffic_acquisition_2026-07.csv
    │   ├── landing_2026-07.csv
    │   └── events_2026-07.csv
    ├── Sitemap/
        └── sitemap_2026-07.csv
    └── WebCoreVitals/
        └── web_core_vitals_2026-07.csv
```

## Streams

Each month produces eleven CSV files per site:

- **GSC queries** / **GSC pages**: all traffic, columns
  `query,clicks,impressions,ctr,position` / `page,clicks,impressions,ctr,position`.
- **GSC Canada queries** / **GSC Canada pages**: filtered with
  `dimensionFilterGroups` (`country equals CAN`), same columns.
- **Bing queries** / **Bing pages**: fetched from `GetQueryStats` and
  `GetPageStats`, respectively. Bing's dated rows are filtered to the requested
  month and aggregated by query/page; columns match the GSC CSVs.
- **Sitemap**: fetched from the site's public `sitemap.xml` endpoint, columns
  `url,lastmod,changefreq,priority`. A sitemap is a point-in-time snapshot of
  the site, not a per-month report, so the same snapshot is stored under each
  month in the site's window. `lastmod` is normalised to `YYYY-MM-DD`
  (timestamp values are truncated to their date); unparseable values are kept
  as-is. Sitemap indexes (`<sitemapindex>` roots and nested indexes, up to a
  depth of four) are followed automatically, and URLs are deduplicated and
  sorted by URL.
- **Crawl**: bounded HTTP crawl of internal URLs discovered through HTML links
  and sitemap files. `Crawls/YYYY-MM.csv` contains URL, final URL, status,
  content type, indexability directives, canonical URL, redirects, and unique
  inlinks. `Images/images_YYYY-MM.csv` contains discovered internal image
  status, type, size, and dimensions when available. This is an HTTP crawler,
  not a browser renderer: links and metadata inserted only by JavaScript may
  not be discovered.
- **Web Core Vitals**: one PageSpeed Insights snapshot for each device strategy,
  stored together in `web_core_vitals_YYYY-MM.csv` with columns
  `device,lcp_ms,inp_ms,cls`. The data is shared per site rather than stored
  under GSC, Bing, or GA4. A missing lab metric is recorded as an empty cell.
- **GA4 traffic acquisition**: `traffic_acquisition_YYYY-MM.csv` from the GA4
  `runReport` endpoint (dimension `sessionSourceMedium`), columns
  `session_source_medium,sessions,engagedSessions,engagementRate,averageSessionDuration,keyEvents,sessionKeyEventRate`.
- **GA4 landing pages**: `landing_YYYY-MM.csv` (dimension `landingPage`) with a
  `TOTAL` row aggregation, columns
  `landing_page,sessions,activeUsers,newUsers,averageEngagementTimePerSession,keyEvents,sessionKeyEventRate`.
- **GA4 events**: `events_YYYY-MM.csv` (dimension `eventName`) filtered to
  `booking_link_click`, `cal_cta_click`, `calendly_cta_click`, columns
  `event_name,eventCount,totalUsers,eventCountPerUser`.

All GA4 reports use the same `runReport` endpoint with the date range adjusted
per month (`dateRanges` with `startDate`/`endDate`).

Example: a monthly run on 22 September 2026 requests 1–31 August 2026 and, if
the July 2026 files are missing, 1–31 July 2026. A quarterly AjayKumar run in
January 2026 backfills July–December 2025; the April run then fetches only
January–March 2026.

Failures are logged per site/month and the CLI exits non-zero so a cron job
alerts; through the HTTP API, the same failures surface as a `failed` run with
per-site/per-stream details. One failing month, stream, or site does not stop
the rest of the run.
