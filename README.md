# Performance audit (Search Console + Bing + GA4)

Collects performance data from Google Search Console and Bing Webmaster
(queries, pages), Google Analytics 4 (traffic by source/medium), sitemap and
Core Web Vitals snapshots, and an HTTP based internal page and image crawl.
Every stream is serialised to a CSV **in memory** and uploaded to **Google
Drive** under the `audit_data/` folder — no CSV is ever written to local
disk, a persistent disk, or a project folder.

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
│   │   └── 2026-08.csv
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
GOOGLE_CLIENT_ID=your_oauth_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_oauth_client_secret
GOOGLE_REFRESH_TOKEN=your_oauth_refresh_token
BING_API_KEY=your-bing-webmaster-api-key
GA4_PROPERTY_ID_DILIGENTIC=your-diligentic-ga4-property-id
GA4_PROPERTY_ID_AJAYKUMAR=your-ajaykumar-ga4-property-id

# Google Drive upload — OAuth 2.0 (personal account)
GOOGLE_OAUTH_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
GOOGLE_DRIVE_REFRESH_TOKEN=your_refresh_token
# Optional: store data inside this folder id instead of the top of My Drive
GOOGLE_DRIVE_ROOT_FOLDER_ID=
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

- **Build command** (installs uv, then installs dependencies):
  ```bash
  uv sync
  uv run fastapi run app.py
  ```

Set the environment variables listed above on the service. `AUDIT_API_KEY`
guards the trigger endpoint the same way it does locally.

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
