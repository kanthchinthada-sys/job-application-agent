"""Find jobs with Apify: structured listings from employer career sites (optional, paid per result).

Actor: Career Site Jobs API (jobo.world/ats-jobs-api) by default; another can be set in APIFY_ACTOR_ID. It searches
live jobs read from employers' own career sites on 200+ applicant tracking systems (Workday, Greenhouse, Lever,
Ashby, iCIMS, SuccessFactors, Taleo ...) and returns the posting on the employer's site (listing_url) and the
employer's application page (apply_url). See docs/apify.md for why this Actor was chosen.

How a search runs
-----------------
* It is an explicit option in Find jobs (it costs money), never part of the free search.
* The backend starts the Actor run through Apify's API (POST /v2/acts/{actor}/runs) and returns at once; the run
  happens on Apify. Find jobs asks for progress by run ID (GET /v2/actor-runs/{id}); when the run has finished the
  dataset is read once, mapped to the app's FoundJob, filtered by your title, country and dates and de-duplicated
  exactly like the free sources, and stored with the run, so asking again costs nothing.
* Limits (in .env): APIFY_MAX_RESULTS (jobs per run), APIFY_TIMEOUT_SECS (the run is stopped after this) and
  APIFY_MAX_COST_USD (Apify stops charging at this amount). They are passed to Apify as the run's own limits.
* One paid run at a time per account: while a run is starting or running, starting another returns that run
  (a double-click, a second tab or a retry after a slow answer never starts a second paid run). A finished run
  with the same filters from the last APIFY_REUSE_MINUTES is reused instead of paying again.
* Runs are stored in the signed-in account's own workspace database, so each account sees only its runs.

Privacy: only the search filters are sent (title, country name, date range, page size). Profiles, resumes and
experience records never leave the app. The token stays on the server (.env) and is never sent to the browser.
"""

import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone

import httpx

from app import job_search

API = "https://api.apify.com/v2"
DEFAULT_ACTOR = "jobo.world/ats-jobs-api"
ACTIVE = ("STARTING", "READY", "RUNNING")
FINISHED_OK = ("SUCCEEDED",)
FAILED = ("FAILED", "ABORTED", "TIMED-OUT", "ERROR")

SCHEMA = """
CREATE TABLE IF NOT EXISTS apify_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    apify_run_id    TEXT,
    dataset_id      TEXT,
    actor           TEXT NOT NULL,
    filters         TEXT NOT NULL,            -- JSON: title, country, date_from, date_to (what was sent)
    filters_key     TEXT NOT NULL,
    status          TEXT NOT NULL,            -- STARTING / READY / RUNNING / SUCCEEDED / FAILED / ABORTED / TIMED-OUT / ERROR
    message         TEXT NOT NULL DEFAULT '',
    max_results     INTEGER NOT NULL,
    timeout_secs    INTEGER NOT NULL,
    max_cost_usd    REAL NOT NULL,
    cost_usd        REAL,
    found           INTEGER,
    results         TEXT,                     -- JSON list of FoundJob (after filtering and de-duplication)
    started_at      TEXT NOT NULL,
    finished_at     TEXT
);
"""

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ApifyError(RuntimeError):
    """A problem the user can act on (shown as is in Find jobs)."""


# --- configuration -----------------------------------------------------------------------------------------

def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except ValueError:
        return default


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(os.environ.get(name, default))))
    except ValueError:
        return default


def settings() -> dict:
    """What the next run would use (the token itself is never returned)."""
    actor = (os.environ.get("APIFY_ACTOR_ID") or DEFAULT_ACTOR).strip()
    return {"configured": bool(os.environ.get("APIFY_API_TOKEN", "").strip()),
            "actor": actor,
            "max_results": _env_int("APIFY_MAX_RESULTS", 50, 1, 500),
            "timeout_secs": _env_int("APIFY_TIMEOUT_SECS", 180, 30, 1800),
            "max_cost_usd": _env_float("APIFY_MAX_COST_USD", 0.50, 0.01, 50.0),
            "reuse_minutes": _env_int("APIFY_REUSE_MINUTES", 60, 0, 1440)}


def _token() -> str:
    token = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not token:
        raise ApifyError("Apify is not set up: add APIFY_API_TOKEN to .env and restart the app.")
    return token


def _actor_path(actor: str) -> str:
    return actor.replace("/", "~")             # the API writes "user/name" as "user~name"


# --- Apify's API ---------------------------------------------------------------------------------------------

def http(method: str, url: str, body: object = None) -> dict:
    """One call to Apify's API with the server-side token. Raises ApifyError with a message for the user."""
    try:
        r = httpx.request(method, url, json=body, timeout=30,
                          headers={"Authorization": f"Bearer {_token()}", "User-Agent": "job-application-agent"})
    except httpx.HTTPError as exc:
        raise ApifyError(f"Could not reach Apify ({exc.__class__.__name__}). Check your connection and try again.") from exc
    if r.status_code == 401:
        raise ApifyError("Apify refused the token (HTTP 401): check APIFY_API_TOKEN in .env.")
    if r.status_code == 402:
        raise ApifyError("Your Apify account has no credit left for this run (HTTP 402).")
    if r.status_code == 404:
        raise ApifyError("Apify could not find that Actor or run (HTTP 404): check APIFY_ACTOR_ID in .env.")
    if r.status_code == 429:
        raise ApifyError("Apify is limiting requests right now (HTTP 429). Try again in a minute.")
    if r.status_code >= 400:
        raise ApifyError(f"Apify answered HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    return data.get("data", data) if isinstance(data, dict) else {"items": data}


def actor_input(title: str, country: job_search.Country, date_from: date, date_to: date, max_results: int) -> dict:
    """Only the search filters: the title, the country and the date range (and how many jobs)."""
    return {"queries": [title], "locations": [country.name], "search_description": False,
            "posted_after": date_from.isoformat(), "posted_before": (date_to + timedelta(days=1)).isoformat(),
            "page_size": max_results, "description_format": "markdown"}


def _start(actor: str, body: dict, s: dict, request) -> dict:
    url = (f"{API}/acts/{_actor_path(actor)}/runs?timeout={s['timeout_secs']}&maxItems={s['max_results']}"
           f"&maxTotalChargeUsd={s['max_cost_usd']}")
    return request("POST", url, body)


# --- mapping one dataset item to the app's job -----------------------------------------------------------------

def _str(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or value.get("display_name") or "").strip()
    return str(value or "").strip()


def _locations(item: dict) -> str:
    raw = item.get("locations") or item.get("location") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    parts = []
    for loc in raw:
        if isinstance(loc, dict):
            bits: list[str] = []
            for k in ("city", "region", "state", "country"):   # "Toronto, ON" (not "Toronto, ON, ON")
                if loc.get(k) and str(loc[k]).strip() not in bits:
                    bits.append(str(loc[k]).strip())
            text = loc.get("location") or ", ".join(bits)
        else:
            text = str(loc)
        text = ", ".join(dict.fromkeys(x.strip() for x in str(text or "").split(",") if x.strip()))   # no repeats
        if text and text not in parts:
            parts.append(text)
    return "; ".join(parts[:3])


def _plain(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"[*_`]{1,3}", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_found_job(item: dict) -> job_search.FoundJob | None:
    """One Actor result as a Find jobs result. Unavailable fields stay blank; the direct employer link is used."""
    title = _str(item.get("title") or item.get("job_title"))
    company = _str(item.get("company") or item.get("company_name") or item.get("organization"))
    listing = _str(item.get("listing_url") or item.get("url"))
    apply = _str(item.get("apply_url"))
    url = listing or apply                     # the posting on the employer's own career site
    if not (title and url):
        return None
    ats = _str(item.get("source") or item.get("ats"))
    workplace = _str(item.get("workplace_type") or item.get("work_model")).lower()
    location = _locations(item)
    remote = "remote" in workplace or "remote" in location.lower()
    return job_search.FoundJob(
        source=f"{ats.title()} career site (via Apify)" if ats else "Employer career site (via Apify)",
        title=title, company=company, location=location, posted=job_search._day(item.get("date_posted")),
        url=url, credit_url=apply if apply and apply != url else "",
        description=_plain(_str(item.get("description"))), remote=remote)


# --- runs ------------------------------------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _filters_key(f: dict) -> str:
    return json.dumps({k: f[k] for k in ("title", "country", "date_from", "date_to")}, sort_keys=True).lower()


def ensure_table(conn) -> None:
    conn.executescript(SCHEMA)


def _lock_for(conn) -> threading.Lock:
    path = conn.execute("PRAGMA database_list").fetchone()[2] or ":memory:"
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


def _row(conn, run_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM apify_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(r) if r else None


def start_run(conn, title: str, country_code: str, date_from: date, date_to: date, request=http) -> dict:
    """Start a paid run, or return the account's run that is already active (or a recent one with the same
    filters). Never two paid runs at once for one account."""
    s = settings()
    if not s["configured"]:
        raise ApifyError("Apify is not set up: add APIFY_API_TOKEN to .env and restart the app.")
    country = job_search.COUNTRIES[country_code]
    filters = {"title": title, "country": country_code, "date_from": date_from.isoformat(),
               "date_to": date_to.isoformat()}
    key = _filters_key(filters)
    ensure_table(conn)
    with _lock_for(conn):
        active = conn.execute(f"SELECT id FROM apify_runs WHERE status IN ({','.join('?' * len(ACTIVE))}) "
                              "ORDER BY id DESC LIMIT 1", ACTIVE).fetchone()
        if active:
            return {**_row(conn, active["id"]), "reused": "active"}
        if s["reuse_minutes"]:
            since = _iso(_now() - timedelta(minutes=s["reuse_minutes"]))
            recent = conn.execute("SELECT id FROM apify_runs WHERE filters_key = ? AND status = 'SUCCEEDED' "
                                  "AND started_at >= ? ORDER BY id DESC LIMIT 1", (key, since)).fetchone()
            if recent:
                return {**_row(conn, recent["id"]), "reused": "recent"}
        cur = conn.execute(
            "INSERT INTO apify_runs (actor, filters, filters_key, status, max_results, timeout_secs, max_cost_usd, "
            "started_at) VALUES (?, ?, ?, 'STARTING', ?, ?, ?, ?)",
            (s["actor"], json.dumps(filters), key, s["max_results"], s["timeout_secs"], s["max_cost_usd"],
             _iso(_now())))
        conn.commit()
        run_id = cur.lastrowid
    # the paid call happens outside the lock; the STARTING row already blocks a second run
    try:
        run = _start(s["actor"], actor_input(title, country, date_from, date_to, s["max_results"]), s, request)
    except ApifyError as exc:
        conn.execute("UPDATE apify_runs SET status = 'ERROR', message = ?, finished_at = ? WHERE id = ?",
                     (str(exc), _iso(_now()), run_id))
        conn.commit()
        raise
    conn.execute("UPDATE apify_runs SET apify_run_id = ?, dataset_id = ?, status = ? WHERE id = ?",
                 (run.get("id"), run.get("defaultDatasetId"), run.get("status") or "READY", run_id))
    conn.commit()
    return {**_row(conn, run_id), "reused": ""}


def refresh(conn, run_id: int, request=http, already_added=None) -> dict:
    """The run's progress: asks Apify while it is active; on success reads its results once and stores them.
    A run past its time limit is stopped (Apify stops it too; this covers a lost answer)."""
    ensure_table(conn)
    row = _row(conn, run_id)
    if row is None:
        raise LookupError(run_id)
    if row["status"] in ACTIVE and row["apify_run_id"]:
        started = datetime.fromisoformat(row["started_at"])
        overdue = _now() - started > timedelta(seconds=row["timeout_secs"] + 60)
        try:
            run = request("GET", f"{API}/actor-runs/{row['apify_run_id']}", None)
            status = run.get("status") or row["status"]
            cost = run.get("usageTotalUsd")
            if status in ACTIVE and overdue:
                request("POST", f"{API}/actor-runs/{row['apify_run_id']}/abort", None)
                status = "TIMED-OUT"
            message = ""
            if status in FINISHED_OK:
                row["results"], row["found"] = _collect(row, run.get("defaultDatasetId") or row["dataset_id"],
                                                        request)
            elif status in FAILED:
                message = {"TIMED-OUT": f"The run took longer than {row['timeout_secs']} seconds and was stopped.",
                           "ABORTED": "The run was stopped.", "FAILED": "The Actor failed on Apify."}.get(status, "")
            conn.execute("UPDATE apify_runs SET status = ?, message = ?, cost_usd = ?, found = ?, results = ?, "
                         "finished_at = ? WHERE id = ?",
                         (status, message, cost, row.get("found"), row.get("results"),
                          _iso(_now()) if status not in ACTIVE else None, run_id))
            conn.commit()
        except ApifyError as exc:
            if overdue:
                conn.execute("UPDATE apify_runs SET status = 'ERROR', message = ?, finished_at = ? WHERE id = ?",
                             (str(exc), _iso(_now()), run_id))
                conn.commit()
            else:
                return {**_out(_row(conn, run_id), already_added), "message": f"{exc} (still waiting for the run)"}
    elif row["status"] in FINISHED_OK and row["apify_run_id"] and row["finished_at"]:
        # Apify adds the per-result charges to a run's cost shortly after it finishes: read it again for a while
        # (a free call), so the cost shown is what was charged
        if _now() - datetime.fromisoformat(row["finished_at"]) < timedelta(minutes=10):
            try:
                run = request("GET", f"{API}/actor-runs/{row['apify_run_id']}", None)
                if run.get("usageTotalUsd") is not None and run["usageTotalUsd"] != row["cost_usd"]:
                    conn.execute("UPDATE apify_runs SET cost_usd = ? WHERE id = ?", (run["usageTotalUsd"], run_id))
                    conn.commit()
            except ApifyError:
                pass
    return _out(_row(conn, run_id), already_added)


def _collect(row: dict, dataset_id: str, request) -> tuple[str, int]:
    data = request("GET", f"{API}/datasets/{dataset_id}/items?clean=true&format=json&limit={row['max_results']}", None)
    items = data.get("items") if isinstance(data, dict) and "items" in data else data
    items = items if isinstance(items, list) else []
    jobs = [j for j in (to_found_job(x) for x in items if isinstance(x, dict)) if j]
    f = json.loads(row["filters"])
    country = job_search.COUNTRIES[f["country"]]
    kept, _ = job_search.filter_jobs({"Apify": jobs}, f["title"], country, f["date_from"], f["date_to"])
    return json.dumps([j.model_dump() for j in kept]), len(items)


def _out(row: dict, already_added=None) -> dict:
    results = [job_search.FoundJob(**j) for j in json.loads(row["results"] or "[]")]
    if already_added is not None:
        for j in results:
            j.already_added_job_id = already_added(j.url)
    return {"id": row["id"], "status": row["status"], "message": row["message"], "actor": row["actor"],
            "filters": json.loads(row["filters"]), "found": row["found"], "kept": len(results),
            "cost_usd": row["cost_usd"], "max_cost_usd": row["max_cost_usd"], "max_results": row["max_results"],
            "timeout_secs": row["timeout_secs"], "started_at": row["started_at"], "finished_at": row["finished_at"],
            "active": row["status"] in ACTIVE, "results": [j.model_dump() for j in results],
            "reused": row.get("reused", "")}


def latest(conn) -> dict | None:
    """The account's most recent run (to show its progress or results after a page reload)."""
    ensure_table(conn)
    r = conn.execute("SELECT id FROM apify_runs ORDER BY id DESC LIMIT 1").fetchone()
    return r["id"] if r else None
