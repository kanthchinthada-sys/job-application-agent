"""Find jobs by title, country and date range across free job sources ("job_search_v1").

Sources (only public, free APIs that are meant to be read by other sites; nothing is scraped):

* Adzuna         country-wide search of many job boards; needs a FREE key (ADZUNA_APP_ID / ADZUNA_APP_KEY in .env)
* Remotive       remote jobs, keyword search
* Jobicy         remote jobs, keyword + country filter
* Arbeitnow      Europe (mostly Germany) and remote jobs
* Company boards the Greenhouse / Lever / Ashby career pages you list in your preferences ("company_boards")

LinkedIn, Indeed and Glassdoor don't allow automated searching, so they are not used.

Every source is asked separately; one failing never stops the others, and the result says which sources
answered. Results are filtered by YOUR three filters (title, country, date range), de-duplicated across sources
(same title and company), and sorted newest first. Nothing is saved until you press Apply.
"""

import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import quote_plus

from pydantic import BaseModel

from app import importers
from app.preferences import CANADA_PLACES, check_title

METHOD = "job_search_v1"
MAX_RESULTS = 300

US_STATES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
             "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
             "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
             "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
             "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
             "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
             "west virginia", "wisconsin", "wyoming", "district of columbia", "san francisco", "seattle", "boston",
             "chicago", "austin", "los angeles", "atlanta", "denver", "new york city", "nyc", "bay area"]
US_CODES = ["al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la",
            "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
            "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc"]
CA_CODES = ["on", "bc", "ab", "qc", "mb", "sk", "ns", "nb", "nl", "pe"]

_EUROPE = ["europe", "eu", "emea", "european union", "eea"]
_APAC = ["apac", "asia", "asia pacific", "asia-pacific"]
_AMERICAS = ["north america", "americas", "na"]


class Country(BaseModel):
    code: str
    name: str
    places: list[str]                # lower-case words that put a location in this country
    regions: list[str] = []          # remote regions that include it
    adzuna: str | None = None        # Adzuna market code
    jobicy: str | None = None        # Jobicy geo slug


COUNTRIES: dict[str, Country] = {c.code: c for c in [
    Country(code="CA", name="Canada", places=CANADA_PLACES, regions=_AMERICAS + ["usa or canada", "us or canada"],
            adzuna="ca", jobicy="canada"),
    Country(code="US", name="United States", places=["united states", "usa", "u.s.", "u.s.a", "us-", "us only"] + US_STATES,
            regions=_AMERICAS + ["usa or canada", "us or canada"], adzuna="us", jobicy="usa"),
    Country(code="GB", name="United Kingdom", places=["united kingdom", "uk", "england", "scotland", "wales",
            "northern ireland", "london", "manchester", "birmingham", "edinburgh", "glasgow", "bristol", "leeds",
            "cambridge, uk", "oxford"], regions=_EUROPE, adzuna="gb", jobicy="uk"),
    Country(code="IE", name="Ireland", places=["ireland", "dublin", "cork", "galway"], regions=_EUROPE,
            jobicy="ireland"),
    Country(code="DE", name="Germany", places=["germany", "deutschland", "berlin", "munich", "münchen", "hamburg",
            "frankfurt", "cologne", "köln", "stuttgart", "düsseldorf", "leipzig"], regions=_EUROPE + ["dach"],
            adzuna="de", jobicy="germany"),
    Country(code="FR", name="France", places=["france", "paris", "lyon", "marseille", "toulouse", "lille"],
            regions=_EUROPE, adzuna="fr", jobicy="france"),
    Country(code="NL", name="Netherlands", places=["netherlands", "holland", "amsterdam", "rotterdam", "utrecht",
            "eindhoven", "the hague"], regions=_EUROPE, adzuna="nl", jobicy="netherlands"),
    Country(code="ES", name="Spain", places=["spain", "españa", "madrid", "barcelona", "valencia", "seville"],
            regions=_EUROPE, adzuna="es", jobicy="spain"),
    Country(code="IT", name="Italy", places=["italy", "italia", "milan", "milano", "rome", "roma", "turin"],
            regions=_EUROPE, adzuna="it", jobicy="italy"),
    Country(code="PL", name="Poland", places=["poland", "polska", "warsaw", "kraków", "krakow", "wrocław", "wroclaw",
            "gdańsk", "gdansk"], regions=_EUROPE, adzuna="pl", jobicy="poland"),
    Country(code="AT", name="Austria", places=["austria", "österreich", "vienna", "wien", "graz"],
            regions=_EUROPE + ["dach"], adzuna="at"),
    Country(code="CH", name="Switzerland", places=["switzerland", "schweiz", "zurich", "zürich", "geneva", "basel",
            "bern"], regions=_EUROPE + ["dach"], adzuna="ch"),
    Country(code="BE", name="Belgium", places=["belgium", "brussels", "antwerp", "ghent"], regions=_EUROPE, adzuna="be"),
    Country(code="SE", name="Sweden", places=["sweden", "stockholm", "gothenburg", "malmö"], regions=_EUROPE,
            jobicy="sweden"),
    Country(code="PT", name="Portugal", places=["portugal", "lisbon", "lisboa", "porto"], regions=_EUROPE,
            jobicy="portugal"),
    Country(code="IN", name="India", places=["india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai",
            "mumbai", "delhi", "new delhi", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad", "kochi"],
            regions=_APAC, adzuna="in", jobicy="india"),
    Country(code="AU", name="Australia", places=["australia", "sydney", "melbourne", "brisbane", "perth", "adelaide",
            "canberra"], regions=_APAC + ["anz"], adzuna="au", jobicy="australia"),
    Country(code="NZ", name="New Zealand", places=["new zealand", "auckland", "wellington", "christchurch"],
            regions=_APAC + ["anz"], adzuna="nz"),
    Country(code="SG", name="Singapore", places=["singapore"], regions=_APAC, adzuna="sg", jobicy="singapore"),
    Country(code="AE", name="United Arab Emirates", places=["united arab emirates", "uae", "dubai", "abu dhabi"],
            regions=["middle east", "mena", "emea"], jobicy="uae"),
    Country(code="ZA", name="South Africa", places=["south africa", "johannesburg", "cape town", "durban"],
            regions=["africa", "emea"], adzuna="za"),
    Country(code="BR", name="Brazil", places=["brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro"],
            regions=["latam", "latin america", "south america", "americas"], adzuna="br", jobicy="brazil"),
    Country(code="MX", name="Mexico", places=["mexico", "méxico", "mexico city", "guadalajara", "monterrey"],
            regions=["latam", "latin america", "north america", "americas"], adzuna="mx", jobicy="mexico"),
]}
_WORLDWIDE = ["worldwide", "anywhere", "global", "remote - anywhere", "any location", "international"]


class FoundJob(BaseModel):
    source: str
    title: str
    company: str
    location: str
    posted: str | None               # YYYY-MM-DD
    url: str                         # where you apply / read the posting
    description: str = ""            # plain text; may be only a short excerpt (see description_is_excerpt)
    description_is_excerpt: bool = False
    remote: bool = False
    where: str = ""                  # why it counts for your country: "Toronto, ON" or "Remote (worldwide)"
    title_match: str = "strong"      # strong | partial
    already_added_job_id: int | None = None


class SourceStatus(BaseModel):
    name: str
    ok: bool
    found: int = 0
    kept: int = 0
    note: str = ""


class SearchResult(BaseModel):
    method: str = METHOD
    query: str
    country: str
    date_from: str
    date_to: str
    results: list[FoundJob]
    sources: list[SourceStatus]


Fetcher = Callable[[str], object]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _text(html: str | None) -> str:
    if not html:
        return ""
    return importers.html_to_text(unescape(html)) if "<" in html or "&lt;" in html else html.strip()


def _day(value) -> str | None:
    """Many date formats -> YYYY-MM-DD (or None)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 10_000_000_000 else value     # ms or s
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        s = str(value).strip().replace("Z", "+00:00")
        if re.fullmatch(r"\d{10,13}", s):
            return _day(int(s))
        s = s.replace(" ", "T", 1) if re.match(r"\d{4}-\d{2}-\d{2} \d", s) else s
        return datetime.fromisoformat(s).date().isoformat()
    except (ValueError, OverflowError, OSError):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
        return m.group(1) if m else None


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def where_for(location: str, country: Country, remote: bool = False) -> str | None:
    """Why this location counts for the country, or None if it doesn't."""
    loc = " ".join((location or "").lower().split())
    for place in country.places:
        if _has_word(loc, place):
            return location.strip() or country.name
    if country.code == "US" and re.search(rf",\s*({'|'.join(US_CODES)})\b", loc) and "canada" not in loc:
        return location.strip()
    if country.code == "CA" and re.search(rf",\s*({'|'.join(CA_CODES)})\b", loc):
        return location.strip()
    if any(w in loc for w in _WORLDWIDE):
        return "Remote (worldwide)"
    is_remote = remote or "remote" in loc
    if is_remote or not loc:
        if any(_has_word(loc, r) for r in country.regions):
            return location.strip() if "remote" in loc else f"Remote ({location.strip()})"
        if is_remote and re.fullmatch(r"\(?\s*remote\s*\)?", loc):
            return "Remote (country not stated: check the posting)"
    return None


def _title_ok(title: str, query: str) -> str | None:
    level, _ = check_title(title, [query])
    return None if level == "none" else level


# ---------------------------------------------------------------------------
# sources: each returns FoundJob objects (already parsed, not yet filtered)
# ---------------------------------------------------------------------------

def src_remotive(fetch: Fetcher, query: str, country: Country) -> list[FoundJob]:
    data = fetch(f"https://remotive.com/api/remote-jobs?search={quote_plus(query)}&limit=100")
    out = []
    for j in data.get("jobs", []) if isinstance(data, dict) else []:
        out.append(FoundJob(source="Remotive", title=str(j.get("title", "")).strip(),
                            company=str(j.get("company_name", "")).strip(),
                            location=str(j.get("candidate_required_location") or "Remote"),
                            posted=_day(j.get("publication_date")), url=str(j.get("url", "")),
                            description=_text(j.get("description")), remote=True))
    return out


def src_jobicy(fetch: Fetcher, query: str, country: Country) -> list[FoundJob]:
    tag = query.strip()[:50]
    geo = f"&geo={country.jobicy}" if country.jobicy else ""
    url = f"https://jobicy.com/api/v2/remote-jobs?count=100{geo}" + (f"&tag={quote_plus(tag)}" if len(tag) >= 3 else "")
    data = fetch(url)
    out = []
    for j in data.get("jobs", []) if isinstance(data, dict) else []:
        geo_text = str(j.get("jobGeo") or "")
        out.append(FoundJob(source="Jobicy", title=unescape(str(j.get("jobTitle", ""))).strip(),
                            company=unescape(str(j.get("companyName", ""))).strip(),
                            location=f"Remote ({geo_text})" if geo_text else "Remote",
                            posted=_day(j.get("pubDate")), url=str(j.get("url", "")),
                            description=_text(j.get("jobDescription") or j.get("jobExcerpt")),
                            description_is_excerpt=not j.get("jobDescription"), remote=True))
    return out


def src_arbeitnow(fetch: Fetcher, query: str, country: Country) -> list[FoundJob]:
    out = []
    for page in (1, 2, 3):
        data = fetch(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        rows = data.get("data", []) if isinstance(data, dict) else []
        for j in rows:
            out.append(FoundJob(source="Arbeitnow", title=str(j.get("title", "")).strip(),
                                company=str(j.get("company_name", "")).strip(),
                                location=str(j.get("location") or "") + (" (Remote)" if j.get("remote") else ""),
                                posted=_day(j.get("created_at")), url=str(j.get("url", "")),
                                description=_text(j.get("description")), remote=bool(j.get("remote"))))
        if len(rows) < 50:
            break
    return out


def src_adzuna(fetch: Fetcher, query: str, country: Country, days: int) -> list[FoundJob]:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    out = []
    for page in (1, 2, 3):
        url = (f"https://api.adzuna.com/v1/api/jobs/{country.adzuna}/search/{page}?app_id={quote_plus(app_id or '')}"
               f"&app_key={quote_plus(app_key or '')}&results_per_page=50&what={quote_plus(query)}"
               f"&max_days_old={max(1, days)}&sort_by=date&content-type=application/json")
        data = fetch(url)
        rows = data.get("results", []) if isinstance(data, dict) else []
        for j in rows:
            loc = (j.get("location") or {}).get("display_name") or country.name
            if country.name.lower() not in loc.lower():
                loc = f"{loc}, {country.name}"           # Adzuna searches one country: say so
            out.append(FoundJob(source="Adzuna", title=_text(j.get("title")),
                                company=str((j.get("company") or {}).get("display_name") or "Unknown company"),
                                location=loc, posted=_day(j.get("created")), url=str(j.get("redirect_url", "")),
                                description=_text(j.get("description")), description_is_excerpt=True))
        if len(rows) < 50:
            break
    return out


def src_boards(fetch: Fetcher, query: str, country: Country, boards: list[str]) -> list[FoundJob]:
    out = []
    for board_url in boards[:30]:
        link = importers.parse_link(board_url)
        for p in importers.list_board(link, fetch):
            out.append(FoundJob(source=f"{p.company} careers", title=p.title, company=p.company,
                                location=p.location or "", posted=p.posted, url=p.source_url,
                                remote="remote" in (p.location or "").lower()))
    return out


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def adzuna_configured() -> bool:
    return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))


def search(query: str, country_code: str, date_from: date, date_to: date, fetch: Fetcher,
           boards: list[str] | None = None) -> SearchResult:
    country = COUNTRIES[country_code]
    days = (date.today() - date_from).days + 1
    tasks: dict[str, Callable[[], list[FoundJob]]] = {
        "Remotive": lambda: src_remotive(fetch, query, country),
        "Jobicy": lambda: src_jobicy(fetch, query, country),
        "Arbeitnow": lambda: src_arbeitnow(fetch, query, country),
    }
    statuses: list[SourceStatus] = []
    if country.adzuna and adzuna_configured():
        tasks["Adzuna"] = lambda: src_adzuna(fetch, query, country, days)
    elif country.adzuna:
        statuses.append(SourceStatus(name="Adzuna", ok=False, note="Off: add a free ADZUNA_APP_ID and ADZUNA_APP_KEY "
                                     "to .env for country-wide results from many job boards."))
    if boards:
        tasks["Company boards"] = lambda: src_boards(fetch, query, country, boards)

    found: dict[str, list[FoundJob]] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            try:
                found[name] = fut.result(timeout=60)
            except Exception as exc:                     # one source failing never stops the others
                msg = str(exc) or exc.__class__.__name__
                statuses.append(SourceStatus(name=name, ok=False, note=f"Not reachable right now: {msg[:160]}"))

    start, end = date_from.isoformat(), date_to.isoformat()
    kept: dict[tuple[str, str], FoundJob] = {}
    for name, jobs in found.items():
        n_kept = 0
        for j in jobs:
            if not j.title or not j.url:
                continue
            level = _title_ok(j.title, query)
            if level is None:
                continue
            if j.posted and not (start <= j.posted <= end):
                continue
            if not j.posted and name not in ("Company boards",):
                continue                                  # undated postings can't be checked against your range
            where = where_for(j.location, country, j.remote)
            if where is None:
                continue
            j.where, j.title_match = where, level
            key = (re.sub(r"\W+", " ", j.title.lower()).strip(), re.sub(r"\W+", " ", j.company.lower()).strip())
            if key in kept:
                # keep the version with the most useful details (full description, direct employer link)
                old = kept[key]
                if (old.description_is_excerpt and not j.description_is_excerpt) or (old.source == "Adzuna" != j.source):
                    kept[key] = j
                continue
            kept[key] = j
            n_kept += 1
        statuses.append(SourceStatus(name=name, ok=True, found=len(jobs), kept=n_kept))
    results = sorted(kept.values(), key=lambda j: (j.posted or "", j.title_match == "strong"), reverse=True)
    order = {"Adzuna": 0, "Remotive": 1, "Jobicy": 2, "Arbeitnow": 3, "Company boards": 4}
    statuses.sort(key=lambda s: order.get(s.name, 9))
    return SearchResult(query=query, country=country.name, date_from=start, date_to=end,
                        results=results[:MAX_RESULTS], sources=statuses)


def default_range(days: int = 7) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=days - 1), today
