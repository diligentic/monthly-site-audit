# Performance audit (Search Console + Bing + GA4)

Collects performance data from Google Search Console and Bing Webmaster
(queries, pages), Google Analytics 4 (traffic by source/medium), sitemap and
Core Web Vitals snapshots, plus five Screaming Frog SEO Spider tab exports and
its Issues Overview report. Every stream is serialised to a CSV **in memory**
and uploaded to **Google Drive** under the `audit_data/` folder. Screaming Frog
artifacts land in a temporary directory that is deleted right after upload — no
crawl CSV is ever written to a persistent disk or a project folder.

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
│   │       ├── images.csv
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

# Screaming Frog CLI (optional on Linux, where `screamingfrogseospider` is on PATH)
# SCREAMING_FROG_PATH=/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher
# SCREAMING_FROG_TIMEOUT_SECONDS=3600
```

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
missing CSVs are fetched and uploaded. Crawl files from the earlier flat
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

## Screaming Frog crawl

The crawl is delegated to the **Screaming Frog SEO Spider** running headless as
a subprocess (`services/screaming_frog.py`). It exports these required tabs:
`Internal`, `H1`, `Meta Description`, `Page Titles`, and `Images`. It also runs
the `Issues:All` bulk export and stores its single Issues Overview summary.
Flow:

1. Python checks the six target CSVs for the month on Google Drive. If all six
   exist, that month's crawl is skipped entirely. Existing flat files are
   migrated into the month folder first, without refetching them.
2. For a month with missing output, Python creates a private temporary folder
   and launches Screaming Frog once with `--export-tabs
   "Internal:All,H1:All,Meta Description:All,Page Titles:All,Images:All"` and
   `--bulk-export "Issues:All"`.
3. The five tab CSVs and `issues_reports/issues_overview_report.csv` are
   located programmatically and read as raw bytes. Missing outputs are
   uploaded under `Crawls/YYYY-MM/` as `internal.csv`, `h1.csv`,
   `meta_description.csv`, `page_titles.csv`, `images.csv`, and `issues.csv`.
   Outputs already present on Drive are not uploaded again.
4. The temporary folder is removed after the uploads and on every crawl or
   upload failure, so crawl artifacts never persist locally.

All six streams share a per-site in-memory cache. If outputs are missing for
more than one historical month, Screaming Frog still runs only once for that
site during the audit run and the resulting bytes are reused for each missing
month. It is launched again only for a later audit run that still has missing
output.

The executable comes from `SCREAMING_FROG_PATH` when set (for example, the
macOS app launcher) and otherwise from `screamingfrogseospider` on `PATH` (the
Linux CLI), so the same code works locally and on Render. To run only the six
required crawl outputs without calling the other audit sources:

```bash
export SCREAMING_FROG_PATH="/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher"
uv run python main.py --crawl-only --site Diligentic --date 2026-09-25
```

Omit `--date` to use today. Omit `--site` to process all sites that are due.

## HTTP API

The collection also runs on demand through a small FastAPI service (`app.py`).
Nothing is fetched at startup or import time — data is only collected when a run
is triggered. The GitHub Actions workflow posts to this API to start a run,
which keeps data collection off the CI runner and on the server. Results are
delivered straight to Google Drive and can be downloaded through the protected
Drive file endpoint.

### Endpoints

| Method | Path                  | Description                                                                                                  |
| ------ | --------------------- | ------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/healthz`            | Liveness probe (no auth).                                                                                    |
| `POST` | `/api/v1/audit/runs`  | Start an audit in the background. Returns `202` with the `run_id`, or `409` if a run is already in progress. |
| `GET`  | `/api/v1/drive/files` | Download a stored CSV by site, provider, and provider-relative path.                                       |

When `AUDIT_API_KEY` is set in the environment, requests must send it as the
`X-Api-Key` header (except `/healthz`). Start a run:

```bash
curl -X POST http://localhost:8000/api/v1/audit/runs \
  -H "X-Api-Key: ${AUDIT_API_KEY}" -H "Content-Type: application/json" \
  -d '{}'
# {"run_id":"...","status":"running"}
```

Download a crawl CSV from a month folder:

```bash
curl --get http://localhost:8000/api/v1/drive/files \
  -H "X-Api-Key: ${AUDIT_API_KEY}" \
  --data-urlencode "site=Diligentic" \
  --data-urlencode "provider=Crawls" \
  --data-urlencode "file_name=2026-08/h1.csv" \
  -o h1.csv
```

The optional JSON body accepts `site` (a site name) and `date` (an anchor date,
useful for testing). A run with an empty body audits every scheduled site; the
quarterly site is skipped automatically outside its quarter months.

### Deployment (Render)

Native web service (not Docker), **Runtime: Python**. No persistent disk is
required — output goes to Google Drive.

- **Build command** (installs uv, Java + Screaming Frog, then dependencies):
  ```bash
  set -e
  sudo apt-get update
  sudo apt-get install -y openjdk-17-jre-headless
  wget -q https://download.screamingfrog.co.uk/products/seo-spider/screamingfrogseospider_24.3_amd64.deb
  sudo dpkg -i screamingfrogseospider_24.3_amd64.deb
  uv sync
  ```
- **Start command**:
  ```bash
  uv run fastapi run app.py
  ```

Set the environment variables listed above on the service. `AUDIT_API_KEY`
guards the trigger endpoint the same way it does locally.

#### Screaming Frog on Linux (Render)

- The Ubuntu `.deb` installs the `screamingfrogseospider` CLI on `PATH`, so
  `SCREAMING_FROG_PATH` is usually unnecessary on Linux. If the binary lands
  elsewhere, set `SCREAMING_FROG_PATH` to its absolute path.
- Screaming Frog is a headless Java application; `openjdk-17-jre-headless`
  (or newer) is required. The `.deb` pulls its X/lib dependencies in.
- A **paid licence** is required to crawl more than 500 URLs and to use
  command-line automation. Activate the licence once in the GUI (which writes
  `screamingfrog.lic`) or place the licence file where the CLI can read it;
  without it the crawl is limited to 500 URLs.
- Large crawls need JVM heap: raise it via the `screamingfrogseospider`
  `.vmoptions` file if the crawl aborts with an out-of-memory error.
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

Each month produces up to seventeen CSV files per site:

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
- **Crawl**: one headless **Screaming Frog SEO Spider** crawl produces the five
  required `:All` tab exports plus the Issues Overview report. They are stored
  in `Crawls/YYYY-MM/` as `internal.csv` (`Internal`), `h1.csv` (`H1`),
  `meta_description.csv` (`Meta Description`), `page_titles.csv`
  (`Page Titles`), `images.csv` (`Images`), and `issues.csv` (`Issues`).
  The original Screaming Frog column sets are preserved. Generated files are
  read into memory, uploaded to Drive, and then removed with their temporary
  directory.
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
