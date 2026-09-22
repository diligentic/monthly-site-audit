# Search Console performance audit

Collects performance data (queries and pages) from Google Search Console for
multiple sites on a monthly or quarterly schedule, storing one CSV file per
site, month, and data stream.

Set these values in `.env` before running the program:

```env
GSC_API_KEY=your-google-api-key
GSC_BEARER_TOKEN=your-oauth-2-access-token
```

Run the audit with:

```bash
uv run python main.py
```

## Sites

| Site | Search Console property | Schedule | Window | Always fetch | Folder |
|---|---|---|---|---|---|
| Diligentic | `sc-domain:diligentic.ca` | Monthly | previous 2 months | newest 1 month | `data/Diligentic/` |
| AjayKumar | `sc-domain:ajaykumar.ca` | Quarterly (Jan, Apr, Jul, Oct) | previous 6 months | newest 3 months | `data/AjayKumar/` |

Within a site's window the **always-fetch** months (the newest data) are
refetched on every run, while the older months are fetched only when their CSV
file is missing. The first run therefore backfills the whole window; later runs
only fetch what is new since the previous run.

Data is stored by site and data source (provider), so adding Bing/GA4 later
only adds new provider folders:

```text
data/
├── Diligentic/
│   └── GSC/
│       ├── queries_2026-08.csv
│       └── pages_2026-08.csv
└── AjayKumar/
    └── GSC/
        ├── queries_2026-07.csv
        └── pages_2026-07.csv
```

Files per month: `queries_YYYY-MM.csv` (columns
`query,clicks,impressions,ctr,position`) and `pages_YYYY-MM.csv` (columns
`page,clicks,impressions,ctr,position`), where `page` holds the page URL.

Example: a monthly run on 22 September 2026 requests 1–31 August 2026 and, if
`queries_2026-07.csv`/`pages_2026-07.csv` are missing, 1–31 July 2026. A
quarterly AjayKumar run in January 2026 backfills July–December 2025; the
April run then fetches only January–March 2026.

Failures are logged per site/month and the script exits non-zero so the cron
job alerts; one failing month or site does not stop the rest of the run.