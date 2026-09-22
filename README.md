# Performance audit (Search Console + Bing + GA4)

Collects performance data from Google Search Console and Bing Webmaster
(queries, pages), plus Google Analytics 4 (traffic by source/medium) and a
monthly sitemap snapshot, for multiple sites on a monthly or quarterly
schedule. It stores one CSV file per site, month, and data stream.

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
fetched from the site's public `sitemap.xml` endpoint.

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
│   ├── Bing/
│   │   ├── queries_2026-08.csv
│   │   └── pages_2026-08.csv
│   ├── GA4/
│   │   ├── traffic_acquisition_2026-08.csv
│   │   ├── landing_2026-08.csv
│   │   └── events_2026-08.csv
│   └── Sitemap/
│       └── sitemap_2026-08.csv
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
    └── Sitemap/
        └── sitemap_2026-07.csv
```

## Streams

Each month produces ten CSV files per site:

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

Failures are logged per site/month and the script exits non-zero so the cron
job alerts; one failing month, stream, or site does not stop the rest of the
run.
