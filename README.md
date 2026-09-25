# Performance audit (Search Console + Bing + GA4)

Collects performance data from Google Search Console and Bing Webmaster
(queries, pages), Google Analytics 4 (traffic by source/medium), sitemap and
Core Web Vitals snapshots, a Screaming Frog SEO Spider internal crawl, and an
HTTP based image crawl. Every stream is serialised to a CSV **in memory** and
uploaded to **Google Drive** under the `audit_data/` folder. The Screaming
Frog export lands in a temporary directory that is deleted right after the
upload — no CSV is ever written to a persistent disk or a project folder.

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
│   │   ├── internal_2026-08.csv
│   │   └── issues_2026-08.csv
│   ├── Images/
│   │   └── images_2026-08.csv
│   └── WebCoreVitals/
│       └── web_core_vitals_2026-08.csv
└── AjayKumar/
    ├── GSC/...
    ├── Bing/...
    ├── GA4/...
    ├── Sitemap/...
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

Uploads are idempotent: if a CSV already exists in its target folder it is
updated in place instead of duplicated. Missing folders (`audit_data`, site
folders, provider folders) are created automatically in your My Drive.

Run the audit locally with:

```bash
uv run fastapi run app.py
```

The CLI and the HTTP API below share the exact same orchestration
(`services/audit_runner.py`), so results are identical either way.

## Screaming Frog crawl

The internal crawl is delegated to the **Screaming Frog SEO Spider** running
headless as a subprocess (`services/screaming_frog.py`). Flow per run:

1. Python creates a private temporary directory (`tempfile.TemporaryDirectory`).
2. The CLI is launched with `--headless --crawl <site_url> --output-folder
   <temp_dir> --export-tabs "Internal:All" --bulk-export "Issues:All"`.
3. After the crawl exits `0`, the generated `internal_all.csv` and the Issues
   Overview report (`issues_reports/issues_overview_report.csv`, a single
   summary of every issue found) are located programmatically and read as raw
   bytes. The remaining per-issue detail CSVs in `issues_reports/` are
   discarded with the temporary directory.
4. The bytes are uploaded to Google Drive under the site's `Crawls/` folder as
   `internal_YYYY-MM.csv` and `issues_YYYY-MM.csv` — the names are chosen by
   Python and are independent of Screaming Frog's own filenames. Uploads are
   idempotent (update in place).
5. The temporary directory is removed after the uploads — and also removed on
   any crawl or upload failure, so nothing ever persists locally.

One crawl per site per run feeds both exports (the two streams share a
per-site in-memory cache), so Screaming Frog is never launched twice. Note
that `Issues:All` is a bulk export rather than an export tab in the CLI, so it
must be passed via `--bulk-export`.

The executable comes from `SCREAMING_FROG_PATH` when set (e.g. the macOS app
launcher) and otherwise from the `screamingfrogseospider` command on `PATH`
(the Linux CLI), so the same code works locally and on Render. `main.py --site
Diligentic` exercises this path; e.g. on macOS:

```bash
export SCREAMING_FROG_PATH="/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher"
uv run python main.py --site Diligentic --date 2026-09-25
```

The `Images/` stream still uses the HTTP crawler; only the `Crawls/` exports
(`internal_*.csv` and `issues_*.csv`) are powered by Screaming Frog.

## HTTP API

The collection also runs on demand through a small FastAPI service (`app.py`).
Nothing is fetched at startup or import time — data is only collected when a run
is triggered. The GitHub Actions workflow posts to this API to start a run,
which keeps data collection off the CI runner and on the server. The API no
longer serves CSVs: results are delivered straight to Google Drive.

### Endpoints

| Method | Path                 | Description                                                                                                  |
| ------ | -------------------- | ------------------------------------------------------------------------------------------------------------ |
| `GET`  | `/healthz`           | Liveness probe (no auth).                                                                                    |
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

| Site       | Search Console property   | GA4 property                     | Schedule                       | Window            | Always fetch    | Drive folder             |
| ---------- | ------------------------- | -------------------------------- | ------------------------------ | ----------------- | --------------- | ------------------------ |
| Diligentic | `sc-domain:diligentic.ca` | env `GA4_PROPERTY_ID_DILIGENTIC` | Monthly                        | previous 2 months | newest 1 month  | `audit_data/Diligentic/` |
| AjayKumar  | `sc-domain:ajaykumar.ca`  | env `GA4_PROPERTY_ID_AJAYKUMAR`  | Quarterly (Jan, Apr, Jul, Oct) | previous 6 months | newest 3 months | `audit_data/AjayKumar/`  |

Within a site's window the **always-fetch** months (the newest data) are
refetched on every run, while the older months are fetched only when their CSV
is missing on Google Drive. The first run therefore backfills the whole window;
later runs only fetch what is new since the previous run.

Data is stored by site and data source (provider), so adding another provider
only adds a new provider folder — see the tree at the top of this document.

## Streams

Each month produces twelve CSV files per site:

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
- **Crawl**: headless **Screaming Frog SEO Spider** crawl of the site.
  `Crawls/internal_YYYY-MM.csv` is the `Internal:All` export (Address, Content
  Type, Status Code, Indexability, Titles, Headings, Canonicals, Inlinks,
  Outlinks, Redirect URL, etc.) and `Crawls/issues_YYYY-MM.csv` is the Issues
  Overview report (every issue found — name, type, priority, offending URL
  count, percent of total, description and how to fix). Both are produced by
  one subprocess crawl (the Issues report via the `Issues:All` bulk export)
  into a temporary directory that is deleted right after the Google Drive
  uploads. `Images/images_YYYY-MM.csv` is
  produced by the in-house HTTP crawler and contains discovered internal image
  status, type, size, and dimensions when available; this crawler is not a
  browser renderer, so links and metadata inserted only by JavaScript may not
  be discovered.
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
