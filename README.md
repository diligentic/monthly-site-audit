# Performance audit (Search Console + GA4)

Collects performance data from Google Search Console (queries, pages) and
Google Analytics 4 (traffic by source/medium) for multiple sites on a monthly
or quarterly schedule, storing one CSV file per site, month, and data stream.

Set these values in `.env` before running the program:

```env
GSC_API_KEY=your-google-api-key
GSC_BEARER_TOKEN=your-oauth-2-access-token
GA4_PROPERTY_ID_DILIGENTIC=your-diligentic-ga4-property-id
GA4_PROPERTY_ID_AJAYKUMAR=your-ajaykumar-ga4-property-id
```

GA4 reuses the Search Console API key and OAuth bearer token; only the GA4
property ID (from GA4 Admin → Property Settings) is needed per site.

Run the audit with:

```bash
uv run python main.py
```

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
│   └── GA4/
│       └── traffic_acquisition_2026-08.csv
└── AjayKumar/
    ├── GSC/
    │   ├── queries_2026-07.csv
    │   ├── pages_2026-07.csv
    │   ├── canada_queries_2026-07.csv
    │   └── canada_pages_2026-07.csv
    └── GA4/
        └── traffic_acquisition_2026-07.csv
```

## Streams

Each month produces five CSV files per site:

- **GSC queries** / **GSC pages**: all traffic, columns
  `query,clicks,impressions,ctr,position` / `page,clicks,impressions,ctr,position`.
- **GSC Canada queries** / **GSC Canada pages**: filtered with
  `dimensionFilterGroups` (`country equals CAN`), same columns.
- **GA4 traffic acquisition**: `traffic_acquisition_YYYY-MM.csv` from the GA4
  `runReport` endpoint (dimension `sessionSourceMedium`), columns
  `session_source_medium,sessions,engagedSessions,engagementRate,averageSessionDuration,keyEvents,sessionKeyEventRate`.

Example: a monthly run on 22 September 2026 requests 1–31 August 2026 and, if
the July 2026 files are missing, 1–31 July 2026. A quarterly AjayKumar run in
January 2026 backfills July–December 2025; the April run then fetches only
January–March 2026.

Failures are logged per site/month and the script exits non-zero so the cron
job alerts; one failing month, stream, or site does not stop the rest of the
run.