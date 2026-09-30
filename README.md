# Monthly Site Audit

This project collects website performance and SEO data for configured sites.
It saves the results as CSV files in Google Drive. You can start a run from the
command line or through the HTTP API.

## What it collects

- Google Search Console search queries and pages
- Google Search Console URL Inspection (Google's own index verdict for every
  crawled page)
- Bing Webmaster search queries and pages
- Google Analytics 4 traffic, landing pages, and selected events
- Sitemap URLs
- PageSpeed Insights results for mobile and desktop
- Website crawl reports covering pages, headings, titles, descriptions, and
  detected issues

Each run fills in missing monthly CSV files and leaves existing files as they
are. Diligentic runs monthly and AjayKumar runs quarterly.

## Where reports are saved

Reports are stored in Google Drive under `audit_data/`, grouped by site and
data source. Crawl reports are grouped by month:

```text
audit_data/
├── Diligentic/
│   ├── GSC/
│   ├── Bing/
│   ├── GA4/
│   ├── Sitemap/
│   ├── Crawls/YYYY-MM/
│   └── WebCoreVitals/
└── AjayKumar/
    └── ...
```

The crawl folder contains `internal.csv`, `h1.csv`, `meta_description.csv`,
`page_titles.csv`, and `issues.csv`.

## Set up

Install [uv](https://docs.astral.sh/uv/) and the project dependencies:

```bash
uv sync
```

Create a `.env` file with the credentials for the services you use:

```env
GSC_API_KEY=your-google-api-key
BING_API_KEY=your-bing-webmaster-api-key
GA4_PROPERTY_ID_DILIGENTIC=your-diligentic-ga4-property-id
GA4_PROPERTY_ID_AJAYKUMAR=your-ajaykumar-ga4-property-id

GOOGLE_OAUTH_CLIENT_ID=your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
GOOGLE_REFRESH_TOKEN=your-refresh-token
GOOGLE_DRIVE_ROOT_FOLDER_ID=

BREVO_API_KEY=your-brevo-api-key
AUDIT_NOTIFICATION_TO_EMAIL=you@example.com
AUDIT_NOTIFICATION_FROM_EMAIL=verified-sender@example.com
AUDIT_NOTIFICATION_FROM_NAME=Site Audit
```

The Google OAuth credentials must have access to the required Search Console,
Analytics, and Drive data. `GOOGLE_DRIVE_ROOT_FOLDER_ID` is optional; if omitted,
the project uses the top level of My Drive.

Audit email notifications are sent through Brevo when all three required
notification values are configured. `AUDIT_NOTIFICATION_TO_EMAIL` accepts a
comma-separated list. The sender address must be verified in Brevo;
`AUDIT_NOTIFICATION_FROM_NAME` is optional. Notification delivery errors are
logged and do not change the audit result.

## Run an audit

Run all sites that are due:

```bash
uv run python main.py
```

Run only one site's crawl reports:

```bash
uv run python main.py --crawl-only --site Diligentic
```

You can provide an anchor date when you need to run for a particular period:

```bash
uv run python main.py --date 2026-09-25
```

## HTTP API

Run the web service locally:

```bash
uv run fastapi run app.py
```

The API provides a health check, an audit trigger, and a way to download a CSV:

| Method | Path                  | Purpose                            |
| ------ | --------------------- | ---------------------------------- |
| `GET`  | `/healthz`            | Check that the service is running. |
| `POST` | `/api/v1/audit/runs`  | Start an audit.                    |
| `GET`  | `/api/v1/drive/files` | Download a CSV from Google Drive.  |

If `AUDIT_API_KEY` is set, include it in the `X-Api-Key` header for the audit
and download endpoints. For example, start an audit with:

```bash
curl -X POST http://localhost:8000/api/v1/audit/runs \
  -H "X-Api-Key: ${AUDIT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"site":"Diligentic","crawl_only":true}'
```

Download a CSV by specifying its site, data source, and path within that source:

```bash
curl --get http://localhost:8000/api/v1/drive/files \
  -H "X-Api-Key: ${AUDIT_API_KEY}" \
  --data-urlencode "site=Diligentic" \
  --data-urlencode "provider=Crawls" \
  --data-urlencode "file_name=2026-08/issues.csv" \
  -o issues.csv
```

The audit request may include `site`, `date`, and `crawl_only`. Without a site,
the run processes all sites that are due. A run already in progress returns
`409`.

## Deployment

The repository includes a GitHub Actions workflow that triggers the API monthly.
Set `AUDIT_API_URL` and `AUDIT_API_KEY` as repository secrets, and configure the
same API key on the service.

For Render, use a native Python web service with:

```text
Build:  uv sync
Start:  uv run fastapi run app.py
```
