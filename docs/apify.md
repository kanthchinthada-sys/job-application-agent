# Apify in Find jobs

Find jobs can run one paid Apify search for structured job listings read straight from employer career sites.
It is optional and explicit: nothing is sent to Apify until you press **Search career sites (Apify)**.

## The Actor: Career Site Jobs API (`jobo.world/ats-jobs-api`)

Chosen on 2026-11-22 from the public Apify Store (the Store API: Actor details, input schemas and pricing).
The Apify MCP connector was still loading in that session, so the same public information was read from the
Store API instead; no Actor was run.

| | Career Site Jobs API (chosen) | Career Site Job Listing API (`fantastic-jobs`) | Career Site ATS Jobs API (`memo23`) |
|---|---|---|---|
| Source | Live jobs read from employers' career sites on 200+ applicant tracking systems (7M+ jobs) | 275k+ company career sites, 58 ATS | 116 ATS, fetched at run time per company |
| Search by title | `queries` (title-only with `search_description: false`) | `titleSearch` | by company, not a title search across all employers |
| Location (Canada) | `locations: ["Canada"]`, geocoded server-side | `locationSearch` | per company |
| Date range | `posted_after` **and** `posted_before`: the same window as Find jobs | `datePostedAfter` or `timeRange` | not a date filter |
| Direct links | `listing_url` (the posting on the employer's site) and `apply_url` (the employer's application page) | `url` | employer ATS link |
| Original source | `source`: the ATS (`workday`, `greenhouse`, `icims` ...) | `source` | ATS |
| Price (free plan) | **$0.004 per job** (paid plans $0.0013-0.0031), start $0.00005 | $0.012 per job, start $0.01 | start $0.03, plus per job and optional emails |
| Users in the last 30 days | 226 | 1,620 | 70 |

Why this one:

* It answers the same question Find jobs asks (a title, a country, a date range) with the same filters, so the
  results can be judged exactly like the free sources.
* It returns the direct employer posting and application links, and the ATS it came from.
* It is a third of the price of the most popular alternative: a 50-job search costs about $0.20 on the free plan.
* It adds what the free sources miss: Workday, iCIMS, SuccessFactors and Taleo career sites, where many large
  Canadian employers post. The free sources cover aggregators (JSearch, Adzuna, Jooble: aggregator links, often
  excerpts), remote-only boards, and only the Greenhouse / Lever / Ashby boards you list yourself.

To check on the first live run: the Actor's documentation does not name Canada explicitly. Its location filter is
geocoded free text, so `Canada` should match Canadian cities; the first run shows it (Find jobs keeps only jobs
whose location is in your country).

To use another Actor, set `APIFY_ACTOR_ID`. The results are read by field name (`title`, `company`,
`listing_url` / `url`, `apply_url`, `locations` / `location`, `date_posted`, `description`, `source`); an Actor
with other names needs a small change in `app/apify_search.py` (`to_found_job`).

## Settings (`.env`)

| Setting | Default | What it does |
|---|---|---|
| `APIFY_API_TOKEN` | (none) | Your Apify API token. Kept on the server; never sent to the browser. |
| `APIFY_ACTOR_ID` | `jobo.world/ats-jobs-api` | The Actor to run. |
| `APIFY_MAX_RESULTS` | 50 | Jobs per run (Apify's `maxItems`, and the Actor's page size). |
| `APIFY_TIMEOUT_SECS` | 180 | The run is stopped after this (Apify's `timeout`; the app also stops a run it hears nothing from). |
| `APIFY_MAX_COST_USD` | 0.50 | The most one run may charge (Apify's `maxTotalChargeUsd`). |
| `APIFY_REUSE_MINUTES` | 60 | A finished search with the same filters is shown again instead of paying again. |

## What is sent, and what stays

Sent to Apify: the job title you typed, the country's name, the date range, and the page size. Nothing else.
Profiles, resumes, cover letters and experience records never leave the app. Runs and their results are stored in
the signed-in account's own workspace, so each account sees only its searches.

## How it runs

1. **Search career sites (Apify)** starts the Actor through Apify's API and returns at once; the free results you
   already have stay on screen and usable.
2. Find jobs checks the run's progress by its run ID every few seconds and shows the status and elapsed time.
3. When the run has finished, its dataset is read once, mapped to Find jobs results, filtered by your title,
   country and dates, and de-duplicated against the results already on screen (same title and company: the
   employer's own link wins over an aggregator's).
4. **Save job** works as for any result: the job opens in the application workspace and its resume and cover
   letter are prepared with the same workflow.

One paid run at a time per account: pressing the button again (a double-click, a second tab, or a retry after a
slow answer) shows the run already in progress instead of starting another.
