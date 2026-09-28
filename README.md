# Performance audit (Search Console + Bing + GA4)

Collects performance data from Google Search Console and Bing Webmaster
(queries, pages), Google Analytics 4 (traffic by source/medium), sitemap and
Core Web Vitals snapshots, plus five internal crawl reports built with
[advertools](https://github.com/nieber/advertools) (Scrapy). Every stream is
serialised to a CSV **in memory** and uploaded to **Google Drive** under the
`audit_data/` folder. Crawl artifacts live in a temporary directory that is
deleted right after upload — no crawl CSV is ever written to a persistent disk
or a project folder.

```
audit_data/
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
│   │   └── sitemap_2026-08.csv
│   ├── Crawls/
│   │   └── 2026-08/
│   │       ├── internal.csv
│   │       ├── h1.csv
│   │       ├── meta_description.csv
│   │       ├── page_titles.csv
│   │       └── issues.csv
│   └── WebCoreVitals/
│       └── web_core_vitals_2026-08.csv
└── AjayKumar/
    ├── GSC/...
    ├── Bing/...
    ├── GA4/...
    ├── Sitemap/...
    ├── Crawls/...
    └── WebCoreVitals/...
```

## Environment variables

Set these values in `.env` before running the program:

```env
GSC_API_KEY=your-google-api-key
BING_API_KEY=your-bing-webmaster-api-key
GA4_PROPERTY_ID_DILIGENTIC=your-diligentic-ga4-property-id
GA4_PROPERTY_ID_AJAYKUMAR=your-ajaykumar-ga4-property-id

GOOGLE_OAUTH_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
GOOGLE_REFRESH_TOKEN=your_refresh_token
# Optional: store data inside this folder id instead of the top of My Drive
GOOGLE_DRIVE_ROOT_FOLDER_ID=
```

The crawl has no credentials. Every knob has a default that fits the Render
Free Tier, so nothing has to be set for it to run; see
[Site crawl](#site-crawl) for the full list.

GA4 reuses the Search Console API key and the Google OAuth credentials above.
The application refreshes access tokens automatically; no manually generated
access token is required. The refresh token must be authorized for the Google
Search Console read-only, Google Analytics read-only, and Google Drive
`drive.file` scopes. Google Drive
uploads continue to use the separate Drive OAuth variables below. Bing uses
its own API key, shared by both sites. Sitemap data needs no credentials: it is
fetched from the site's public `sitemap.xml` endpoint. Core Web Vitals uses the
same Google API key as Search Console.

Before collecting any stream, the runner checks its exact target path on Drive.
An existing monthly CSV is left untouched and its source is not called; only
missing CSVs are fetched and uploaded. Drive uploads use resumable sessions, so
large crawl exports are supported. Crawl files from the earlier flat
`Crawls/<name>_YYYY-MM.csv` layout are moved (without downloading or refetching
their contents) into `Crawls/YYYY-MM/<name>.csv` when the canonical file is
missing. A migration never overwrites a canonical file. Missing folders
(`audit_data`, site, provider, and month folders) are created automatically in
your My Drive.

Run the full audit locally with:

```bash
uv run python main.py
```

The CLI and the HTTP API below share the exact same orchestration
(`services/audit_runner.py`), so results are identical either way.

## Site crawl

The crawl is delegated to **advertools** (`advertools.crawl()`, a Scrapy spider
in `services/crawl.py`). It is a pure-Python dependency: no Java runtime, no
external crawler binary, and no licence. Only the configured domain is crawled;
`www` and the apex host are both treated as internal, and external links are
counted but never followed.

Flow:

1. Python checks the five target CSVs for the month on Google Drive. If all five
   exist, that month's crawl is skipped entirely. Existing flat files are
   migrated into the month folder first, without refetching them.
2. For a month with missing output, Python creates a private temporary folder and
   runs one `advertools.crawl()` over the site, capped by the URL, depth, and
   wall-clock limits below.
3. The jsonlines output is read line by line and reduced to the five reports in
   `utils/crawl_report.py`, then serialised in memory and uploaded to
   `Crawls/YYYY-MM/` as `internal.csv`, `h1.csv`, `meta_description.csv`,
   `page_titles.csv`, and `issues.csv`. Outputs already present on Drive are not
   uploaded again.
4. The temporary folder is removed after the uploads and on every crawl or
   upload failure, so crawl artifacts never persist locally.

All five reports share a per-site in-memory cache. If outputs are missing for
more than one historical month, the site is still crawled only once during the
audit run and the resulting rows are reused for each missing month. It is crawled again only for a later audit run that still has missing output.

A crawl that produces no usable response fails the run instead of uploading
empty reports: advertools does not report a failed crawl, so a site that cannot
be reached would otherwise yield five all-error CSVs. A *partially* failed
crawl is normal and still produces reports, with the errored URLs listed in
`issues.csv`.

To run only the five crawl reports without calling the other audit sources:

```bash
uv run python main.py --crawl-only --site Diligentic --date 2026-09-25
```

Omit `--date` to use today. Omit `--site` to process all sites that are due.
This uploads to Google Drive, so verify a run with the read-only Drive listing
in [Checking a run on Drive](#checking-a-run-on-drive).

### Report columns

| File                   | Columns                                                                                                                                                                                            |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `internal.csv`         | `URL,Status Code,Indexability,Indexability Status,Indexability Reason,Canonical URL,Inlinks,Unique Inlinks,Outlinks,Unique Outlinks,External Outlinks,Word Count,Content Type,Crawl Depth`            |
| `h1.csv`               | `URL,H1,H1 count,H1 length` (one row per H1; a page without an H1 gets one empty row)                                                                                                                 |
| `meta_description.csv` | `URL,Meta Description,Meta Description length,Missing Meta Description`                                                                                                                              |
| `page_titles.csv`      | `URL,Page Title,Page Title length,Missing Page Title`                                                                                                                                                 |
| `issues.csv`           | `URL,Issue,Category,Severity,Details` — one row per URL **and** detected issue, never a summary                                                                                                    |

`issues.csv` reports: missing, duplicate, too short, and too long page titles;
the same four for meta descriptions; missing H1; multiple H1s; duplicate H1;
missing canonical; a canonical pointing at a non-200 URL; broken internal
links; internal links to a redirect; internal links that were never crawled
(page/depth/time budget); and every internal URL that is not a 200. Content
elements are only checked on 2xx HTML documents, so a PDF or a 404 page is
reported through its status code instead of as "missing title".

The two title/description CSVs only flag a missing element on a page the
content check applies to. A PDF, a 404, or a redirect keeps an empty cell and is
**not** flagged, so the CSVs and `issues.csv` always agree.

### Crawl environment variables

| Variable                             | Default                       | Purpose                                                     |
| ------------------------------------ | ----------------------------- | ----------------------------------------------------------- |
| `CRAWL_MAX_URLS`                     | `2000`                        | Hard page-count cap (`CLOSESPIDER_PAGECOUNT`).              |
| `CRAWL_MAX_DEPTH`                    | `10`                          | Maximum link depth, minimum `1` (the start URL is depth 0). |
| `CRAWL_TIMEOUT_SECONDS`              | `1800`                        | Wall-clock cap for one crawl.                               |
| `CRAWL_REQUEST_TIMEOUT_SECONDS`      | `20`                          | Per-request timeout.                                        |
| `CRAWL_CONCURRENT_REQUESTS`          | `4`                           | Parallel downloads (also capped per domain).                |
| `CRAWL_DOWNLOAD_DELAY`               | `0.25`                        | Delay between requests to the same domain, in seconds.      |
| `CRAWL_RETRY_TIMES`                  | `2`                           | Retries per request.                                        |
| `CRAWL_USER_AGENT`                   | `DiligenticSiteAudit/1.0 ...` | Crawler user agent.                                         |
| `CRAWL_ROBOTS_TXT`                   | `1`                           | Obey `robots.txt`; set to `0` only for testing.             |
| `CRAWL_RESPONSE_SIZE_LIMIT_MB`       | `5.0`                         | Responses larger than this are skipped.                     |
| `CRAWL_EXCLUDE_URL_PARAMS`           | `utm_*,gclid,fbclid,mc_*`     | Comma-separated query parameters that do not identify a page. |
| `CRAWL_LOG_LEVEL`                    | `INFO`                        | Scrapy log level.                                           |
| `CRAWL_TITLE_MIN_LENGTH`             | `15`                          | Below this a title is "too short".                          |
| `CRAWL_TITLE_MAX_LENGTH`             | `60`                          | Above this a title is "too long".                           |
| `CRAWL_META_DESCRIPTION_MIN_LENGTH`  | `70`                          | Below this a description is "too short".                    |
| `CRAWL_META_DESCRIPTION_MAX_LENGTH`  | `160`                         | Above this a description is "too long".                     |

The title and description boundaries match the historical crawl CSVs already on
Drive, so new reports stay comparable with them.

### Known limitations

- Scrapy follows redirects and yields one record per final URL. The hop is
  restored from the redirect chain (with its real status and destination), but
  only when the destination had not already been requested — a URL that is both
  linked directly and redirected to is reported once, under its final URL.
- The crawler is a best-effort audit crawl, not a validator: JavaScript-rendered
  content is not executed, so titles, H1s, and meta descriptions are read from
  the served HTML.
- Pages blocked by `robots.txt`, larger than the response limit, or beyond the
  URL/depth/time budget appear in `issues.csv` as "Internal link not crawled"
  rather than being fetched.

### Checking a run on Drive

Both the CLI and the API write the same files, so a run can be verified
straight from the production Drive client instead of the browser:

```bash
uv run python -c "
from dotenv import load_dotenv; load_dotenv()
from services.drive import GoogleDriveStorage
for item in GoogleDriveStorage().list_tree('Diligentic/Crawls'):
    print(item['type'], item['path'], item.get('size', ''))
"
```

This is read-only: it creates nothing and never downloads a file body. The five
CSVs of a month appear as `Diligentic/Crawls/YYYY-MM/<report>.csv`.

## HTTP API

The collection also runs on demand through a small FastAPI service (`app.py`).
Nothing is fetched at startup or import time — data is only collected when a run
is triggered. The GitHub Actions workflow posts to this API to start a run,
which keeps data collection off the CI runner and on the server. Results are
delivered straight to Google Drive and can be listed or downloaded through the
protected Drive endpoints.

### Endpoints

| Method | Path                  | Description                                                                                                  |
| ------ | --------------------- | ------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/healthz`            | Liveness probe (no auth).                                                                                    |
| `POST` | `/api/v1/audit/runs`  | Start an audit in the background. Returns `202` with the `run_id`, or `409` if a run is already in progress. |
| `GET`  | `/api/v1/drive/contents` | List uploaded folders and files below a site/provider, recursively.                                      |
| `GET`  | `/api/v1/drive/files` | Download a stored CSV by site, provider, and provider-relative path.                                       |

When `AUDIT_API_KEY` is set in the environment, requests must send it as the
`X-Api-Key` header (except `/healthz`). Start a run:

```bash
curl -X POST http://localhost:8000/api/v1/audit/runs \
  -H "X-Api-Key: ${AUDIT_API_KEY}" -H "Content-Type: application/json" \
  -d '{}'
# {"run_id":"...","status":"running"}
```

List the uploaded crawl folders and files (the response includes folder names,
file names, paths, sizes, and modification times):

```bash
curl --get http://localhost:8000/api/v1/drive/contents \
  -H "X-Api-Key: ${AUDIT_API_KEY}" \
  --data-urlencode "site=Diligentic" \
  --data-urlencode "provider=Crawls" \
  --data-urlencode "folder=2026-08"
```

The endpoint is read-only and does not create missing Drive folders. Omit
`folder` to list the provider and all of its descendants.

Download a crawl CSV from a month folder:

```bash
curl --get http://localhost:8000/api/v1/drive/files \
  -H "X-Api-Key: ${AUDIT_API_KEY}" \
  --data-urlencode "site=Diligentic" \
  --data-urlencode "provider=Crawls" \
  --data-urlencode "file_name=2026-08/h1.csv" \
  -o h1.csv
```

The optional JSON body accepts `site` (a site name), `date` (an anchor date,
useful for testing), and `crawl_only` (set to `true` to run only the five
crawl reports). A run with an empty body audits every scheduled site; the
quarterly site is skipped automatically outside its quarter months. The endpoint
returns `202` as soon as the background run starts; wait for the
`Audit run <id> finished` log entry before checking Drive.

### Deployment (Render)

Use a **native Python** web service with `scripts/render_build.sh` as the build
command and `uv run fastapi run app.py --host 0.0.0.0 --port $PORT` as the start
command. The build runs `uv sync --frozen`, which installs the locked
dependencies; the crawler is a pure-Python dependency, so the build needs no
root, `sudo`, Java, or external binary. No persistent disk is required because
audit CSVs are uploaded to Google Drive.

Set the environment variables listed above on the service. `AUDIT_API_KEY`
guards the trigger endpoint the same way it does locally.

#### Crawling on Render (Free Tier)

- The defaults are sized for a 512 MB / 0.1 CPU container: 4 parallel requests,
  a 0.25 s delay per domain, a 2,000 URL cap, and a 30-minute wall-clock cap.
  Raise `CRAWL_MAX_URLS` or `CRAWL_TIMEOUT_SECONDS` for a large site, and
  `CRAWL_MAX_DEPTH` if the site nests deeply.
- Free instances spin down when idle and the process can be frozen between
  requests; a crawl that is cut short leaves a missing month on Drive and the
  next run backfills it, because existing CSVs are never overwritten.
- The crawl is triggered inside the FastAPI process (a background thread), not
  on the GitHub Actions runner — the workflow only posts to the API.

### GitHub Actions

The workflow in `.github/workflows/monthly-audit.yml` starts a run on the 5th
of each month (and on demand via `workflow_dispatch`). It validates that both
secrets are set, posts to `POST /api/v1/audit/runs`, and fails the job if the
API does not return `202`. Set two secrets on the repository:

- `AUDIT_API_URL` — the deployed API base URL (no trailing slash).
- `AUDIT_API_KEY` — the same value as `AUDIT_API_KEY` on the server.

A run triggered twice while the first is still running returns `409`.
Failures are written to the application logs and visible as missing or partial
CSVs on Google Drive; the audit does not block on them — one failing stream or
month does not stop the rest of the run.

## Sites

| Site       | Search Console property   | GA4 property                     | Schedule                       | Window            | Drive folder             |
| ---------- | ------------------------- | -------------------------------- | ------------------------------ | ----------------- | ------------------------ |
| Diligentic | `sc-domain:diligentic.ca` | env `GA4_PROPERTY_ID_DILIGENTIC` | Monthly                        | previous 2 months | `audit_data/Diligentic/` |
| AjayKumar  | `sc-domain:ajaykumar.ca`  | env `GA4_PROPERTY_ID_AJAYKUMAR`  | Quarterly (Jan, Apr, Jul, Oct) | previous 6 months | `audit_data/AjayKumar/`  |

Every month in the configured window is checked independently. Any exact CSV
already on Google Drive is skipped and retained; no source is called for that
file and no existing file is overwritten. The first run therefore backfills
only missing files, and later runs collect only files that are still missing.

Data is stored by site and data source (provider), so adding another provider
only adds a new provider folder — see the tree at the top of this document.

## Streams

Each month produces up to sixteen CSV files per site (4 Search Console, 2 Bing,
1 sitemap, 5 crawl, 1 Core Web Vitals, 3 GA4):

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
- **Crawl**: one `advertools` (Scrapy) crawl of the site produces the five
  reports described in [Site crawl](#site-crawl). They are stored in
  `Crawls/YYYY-MM/` as `internal.csv`, `h1.csv`, `meta_description.csv`,
  `page_titles.csv`, and `issues.csv`. The crawl output is read into memory,
  uploaded to Drive, and then removed with its temporary directory.
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
