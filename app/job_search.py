"""Find jobs by title, country and date range across free job sources ("job_search_v1").

Sources (only public, free APIs that are meant to be read by other sites; nothing is scraped):

* JSearch        Google for Jobs results: LinkedIn, Indeed, Glassdoor, ZipRecruiter and company sites; needs a FREE
                 key in JSEARCH_API_KEY: from OpenWeb Ninja ("ak_...", 100 requests a month, 1 per search) or
                 RapidAPI (200 requests a month, 2 per search)
* Adzuna         country-wide search of many job boards; needs a FREE key (ADZUNA_APP_ID / ADZUNA_APP_KEY in .env)
* Jooble         country-wide search of many job boards; needs a FREE key (JOOBLE_API_KEY in .env)
* The Muse       curated company jobs in major cities and remote (no key)
* Himalayas      remote jobs with country eligibility (no key)
* RemoteOK       remote jobs (no key)
* Remotive       remote jobs, keyword search
* Jobicy         remote jobs, keyword + country filter
* Arbeitnow      Europe (mostly Germany) and remote jobs
* Company boards the Greenhouse / Lever / Ashby career pages you list in your preferences ("company_boards")

LinkedIn, Indeed, Glassdoor and hiring.cafe don't allow automated searching (hiring.cafe blocks bots), so the
app doesn't read them directly: JSearch covers the first three through Google for Jobs, and "Search elsewhere"
links open your search on each site in your browser.

Sources that ask for credit (Himalayas, RemoteOK, The Muse) get a visible "via ..." link on every job.

Every source is asked separately; one failing never stops the others, and the result says which sources
answered. Results are filtered by YOUR three filters (title, country, date range), de-duplicated across sources
(same title and company), and sorted newest first. Nothing is saved until you press Apply.
"""

import json
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import quote, quote_plus

import httpx

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
    credit_url: str = ""             # the posting on the source site (some sources require this link)
    title_match: str = "strong"      # strong | partial
    already_added_job_id: int | None = None


class SourceStatus(BaseModel):
    name: str
    ok: bool
    found: int = 0
    kept: int = 0
    note: str = ""


class ElsewhereLink(BaseModel):
    name: str
    url: str
    note: str = ""


class SearchResult(BaseModel):
    method: str = METHOD
    query: str
    country: str
    date_from: str
    date_to: str
    results: list[FoundJob]
    sources: list[SourceStatus]
    elsewhere: list[ElsewhereLink] = []      # the same search on sites the app can't read: opens in your browser


Fetcher = Callable[[str], object]
# (method, url, headers, json body) -> parsed JSON: for sources that need a key in a header or a POST
Requester = Callable[[str, str, dict, object], object]


def http_request(method: str, url: str, headers: dict | None = None, body: object = None) -> object:
    try:
        r = httpx.request(method, url, headers={"User-Agent": "job-application-agent (personal use)", **(headers or {})},
                          json=body, timeout=25)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"could not reach {url.split('/')[2]}: {exc.__class__.__name__}") from exc
    if r.status_code in (401, 403):
        raise RuntimeError(f"the key was refused (HTTP {r.status_code}): check it in .env")
    if r.status_code == 429:
        raise RuntimeError("the free monthly limit is used up (HTTP 429)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def _rows(data, *keys: str) -> list[dict]:
    """The list of jobs in an API answer, wherever it is."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return [x for x in data[k] if isinstance(x, dict)]
    return []


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
    """The latest jobs for the country (filtered by title here), plus Jobicy's own keyword search when the title
    fits its limit (3-20 characters)."""
    geo = f"&geo={country.jobicy}" if country.jobicy else ""
    urls = [f"https://jobicy.com/api/v2/remote-jobs?count=100{geo}"]
    tag = query.strip()
    if 3 <= len(tag) <= 20:
        urls.append(f"https://jobicy.com/api/v2/remote-jobs?count=100{geo}&tag={quote_plus(tag)}")
    out, seen = [], set()
    for url in urls:
        data = fetch(url)
        for j in data.get("jobs", []) if isinstance(data, dict) else []:
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
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


# cities The Muse lists jobs under (it has no country filter); remote jobs are listed as "Flexible / Remote"
MUSE_CITIES = {
    "CA": ["Toronto, Canada", "Vancouver, Canada", "Montreal, Canada", "Ottawa, Canada", "Calgary, Canada",
           "Waterloo, Canada", "Edmonton, Canada", "Mississauga, Canada"],
    "US": ["New York, NY", "San Francisco, CA", "Seattle, WA", "Austin, TX", "Boston, MA", "Chicago, IL",
           "Los Angeles, CA", "Atlanta, GA", "Denver, CO", "Washington, DC"],
    "GB": ["London, United Kingdom", "Manchester, United Kingdom", "Edinburgh, United Kingdom"],
    "IN": ["Bangalore, India", "Hyderabad, India", "Pune, India", "Chennai, India"],
    "DE": ["Berlin, Germany", "Munich, Germany"],
    "AU": ["Sydney, Australia", "Melbourne, Australia"],
}
MUSE_CATEGORIES = ["Software Engineering", "Data and Analytics", "Data Science", "IT", "Computer and IT",
                   "Design and UX", "Product", "Project Management"]


def src_muse(fetch: Fetcher, query: str, country: Country) -> list[FoundJob]:
    params = "".join(f"&category={quote_plus(c)}" for c in MUSE_CATEGORIES)
    params += "".join(f"&location={quote_plus(c)}" for c in MUSE_CITIES.get(country.code, []) + ["Flexible / Remote"])
    out = []
    for page in range(0, 5):
        data = fetch(f"https://www.themuse.com/api/public/jobs?page={page}&descending=true{params}")
        rows = _rows(data, "results")
        for j in rows:
            locs = [str(x.get("name") if isinstance(x, dict) else x) for x in (j.get("locations") or [])]
            # keep the locations in this country (a "Flexible / Remote" job is checked by the usual rules)
            here = [x for x in locs if where_for(x, country, "remote" in x.lower())] or locs
            landing = str((j.get("refs") or {}).get("landing_page") or j.get("url") or "")
            out.append(FoundJob(source="The Muse", title=str(j.get("name") or j.get("title") or "").strip(),
                                company=str((j.get("company") or {}).get("name") or "").strip(),
                                location="; ".join(here) or "Not stated",
                                posted=_day(j.get("publication_date")), url=landing, credit_url=landing,
                                description=_text(j.get("contents")),
                                remote=any("remote" in x.lower() or "flexible" in x.lower() for x in here)))
        if len(rows) < 20:
            break
    return out


def src_himalayas(fetch: Fetcher, query: str, country: Country) -> list[FoundJob]:
    out = []
    for page in (1, 2, 3):
        data = fetch(f"https://himalayas.app/jobs/api/search?q={quote_plus(query)}&country={country.code}"
                     f"&sort=recent&page={page}")
        rows = _rows(data, "jobs", "data", "results")
        for j in rows:
            limits = [str(x.get("name") if isinstance(x, dict) else x) for x in (j.get("locationRestrictions") or [])]
            loc = f"Remote ({', '.join(limits)})" if limits else "Remote (worldwide)"
            guid = str(j.get("guid") or "")
            credit = guid if guid.startswith("http") else "https://himalayas.app/jobs"
            out.append(FoundJob(source="Himalayas", title=str(j.get("title") or "").strip(),
                                company=str(j.get("companyName") or "").strip(), location=loc,
                                posted=_day(j.get("pubDate")),
                                url=str(j.get("applicationLink") or credit),   # their terms: apply link unchanged
                                credit_url=credit, description=_text(j.get("description") or j.get("excerpt")),
                                description_is_excerpt=not j.get("description"), remote=True))
        if len(rows) < 20:
            break
    return out


def _remoteok_tag(query: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in {"senior", "sr", "junior", "jr", "lead",
                                                                               "staff", "principal", "remote"}]
    return "-".join(words[:2]) if words else ""


def src_remoteok(fetch: Fetcher, query: str, country: Country) -> list[FoundJob]:
    urls = ["https://remoteok.com/api"]
    tag = _remoteok_tag(query)
    if tag:
        urls.append(f"https://remoteok.com/api?tag={quote_plus(tag)}")
        if "-" in tag:
            urls.append(f"https://remoteok.com/api?tag={quote_plus(tag.split('-')[0])}")
    out, seen = [], set()
    for url in urls:
        for j in _rows(fetch(url)):
            if not j.get("position") or j.get("id") in seen:     # the first item is RemoteOK's legal notice
                continue
            seen.add(j.get("id"))
            page = str(j.get("url") or "")
            out.append(FoundJob(source="RemoteOK", title=unescape(str(j.get("position"))).strip(),
                                company=unescape(str(j.get("company") or "")).strip(),
                                location=f"Remote ({j['location']})" if j.get("location") else "Remote",
                                posted=_day(j.get("date") or j.get("epoch")), url=page or str(j.get("apply_url") or ""),
                                credit_url=page, description=_text(j.get("description")), remote=True))
    return out


def jsearch_configured() -> bool:
    return bool(os.environ.get("JSEARCH_API_KEY"))


def jooble_configured() -> bool:
    return bool(os.environ.get("JOOBLE_API_KEY"))


def _jsearch_window(days: int) -> str:
    return "today" if days <= 1 else "3days" if days <= 3 else "week" if days <= 7 else "month" if days <= 31 else "all"


def src_jsearch(request: Requester, query: str, country: Country, days: int) -> list[FoundJob]:
    """Google for Jobs (LinkedIn, Indeed, Glassdoor, ZipRecruiter, company sites) through JSearch on RapidAPI."""
    key = os.environ.get("JSEARCH_API_KEY", "")
    params = (f"query={quote_plus(f'{query} in {country.name}')}&page=1&country={country.code.lower()}"
              f"&date_posted={_jsearch_window(days)}")
    if key.startswith("ak_"):
        # a key from OpenWeb Ninja (the company behind JSearch): same data, their own address; the free plan has
        # 100 requests a month, so one page (10 jobs) per search
        data = request("GET", f"https://api.openwebninja.com/jsearch/search-v2?{params}&num_pages=1",
                       {"X-API-Key": key}, None)
    else:                                   # a RapidAPI key
        data = request("GET", f"https://jsearch.p.rapidapi.com/search?{params}&num_pages=2",
                       {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}, None)
    rows = _rows(data, "data", "jobs")
    if not rows and isinstance(data, dict) and isinstance(data.get("data"), dict):
        rows = _rows(data["data"], "jobs", "data", "results")       # search-v2 may nest the list
    out = []
    for j in rows:
        parts = [j.get("job_city"), j.get("job_state"), j.get("job_country")]
        loc = j.get("job_location") or ", ".join(str(p) for p in parts if p) or country.name
        if country.name.lower() not in loc.lower() and (j.get("job_country") or "").upper() == country.code:
            loc = f"{loc}, {country.name}"
        remote = bool(j.get("job_is_remote"))
        publisher = str(j.get("job_publisher") or "").strip()
        out.append(FoundJob(source=f"{publisher} (via JSearch)" if publisher else "JSearch",
                            title=str(j.get("job_title") or "").strip(),
                            company=str(j.get("employer_name") or "").strip(),
                            location=loc + (" (Remote)" if remote and "remote" not in loc.lower() else ""),
                            posted=_day(j.get("job_posted_at_datetime_utc") or j.get("job_posted_at_timestamp")),
                            url=str(j.get("job_apply_link") or ""), description=_text(j.get("job_description")),
                            remote=remote))
    return out


def src_jooble(request: Requester, query: str, country: Country) -> list[FoundJob]:
    key = os.environ.get("JOOBLE_API_KEY", "")
    out = []
    for page in (1, 2):
        data = request("POST", f"https://jooble.org/api/{quote(key)}", {"Content-Type": "application/json"},
                       {"keywords": query, "location": country.name, "page": str(page)})
        rows = _rows(data, "jobs")
        for j in rows:
            loc = str(j.get("location") or country.name)
            if country.name.lower() not in loc.lower():
                loc = f"{loc}, {country.name}"           # searched in this country: say so
            out.append(FoundJob(source="Jooble", title=_text(j.get("title")), company=str(j.get("company") or "").strip(),
                                location=loc, posted=_day(j.get("updated")), url=str(j.get("link") or ""),
                                description=_text(j.get("snippet")), description_is_excerpt=True,
                                remote="remote" in (str(j.get("type") or "") + loc).lower()))
        if len(rows) < 20:
            break
    return out


INDEED_HOSTS = {"CA": "ca.indeed.com", "GB": "uk.indeed.com", "IN": "in.indeed.com", "AU": "au.indeed.com",
                "DE": "de.indeed.com", "FR": "fr.indeed.com", "NL": "nl.indeed.com", "IE": "ie.indeed.com",
                "SG": "sg.indeed.com", "AE": "ae.indeed.com", "ES": "es.indeed.com", "IT": "it.indeed.com"}


def elsewhere_links(query: str, country: Country, days: int) -> list[ElsewhereLink]:
    """The same search on sites that don't allow automated searching: they open in your browser."""
    q, place = quote_plus(query), quote_plus(country.name)
    links = [
        ElsewhereLink(name="hiring.cafe", note="set the country in its filters",
                      url="https://hiring.cafe/?searchState=" + quote(json.dumps(
                          {"searchQuery": query, "dateFetchedPastNDays": max(1, days)}, separators=(",", ":")))),
        ElsewhereLink(name="LinkedIn", url=f"https://www.linkedin.com/jobs/search/?keywords={q}&location={place}"
                      f"&f_TPR=r{max(1, days) * 86400}"),
        ElsewhereLink(name="Indeed", url=f"https://{INDEED_HOSTS.get(country.code, 'www.indeed.com')}/jobs?q={q}"
                      f"&l={'' if country.code in INDEED_HOSTS else place}&fromage={min(max(1, days), 14)}"),
        ElsewhereLink(name="Glassdoor", url=f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}"
                      f"&locKeyword={place}"),
        ElsewhereLink(name="Google Jobs", url=f"https://www.google.com/search?q={quote_plus(query + ' jobs ' + country.name)}"
                      "&ibp=htl;jobs"),
    ]
    if country.code == "CA":
        links.append(ElsewhereLink(name="Job Bank (Government of Canada)",
                                   url=f"https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring={q}&locationstring=Canada"))
    return links


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def adzuna_configured() -> bool:
    return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))


def search(query: str, country_code: str, date_from: date, date_to: date, fetch: Fetcher,
           boards: list[str] | None = None, request: Requester | None = None) -> SearchResult:
    request = request or http_request
    country = COUNTRIES[country_code]
    days = (date.today() - date_from).days + 1
    tasks: dict[str, Callable[[], list[FoundJob]]] = {
        "Remotive": lambda: src_remotive(fetch, query, country),
        "Jobicy": lambda: src_jobicy(fetch, query, country),
        "Arbeitnow": lambda: src_arbeitnow(fetch, query, country),
        "The Muse": lambda: src_muse(fetch, query, country),
        "Himalayas": lambda: src_himalayas(fetch, query, country),
        "RemoteOK": lambda: src_remoteok(fetch, query, country),
    }
    statuses: list[SourceStatus] = []
    if jsearch_configured():
        tasks["JSearch"] = lambda: src_jsearch(request, query, country, days)
    else:
        statuses.append(SourceStatus(name="JSearch", ok=False, note="Off: add a free JSEARCH_API_KEY (RapidAPI) to .env "
                                     "for LinkedIn, Indeed and Glassdoor jobs through Google for Jobs."))
    if jooble_configured():
        tasks["Jooble"] = lambda: src_jooble(request, query, country)
    else:
        statuses.append(SourceStatus(name="Jooble", ok=False, note="Off: add a free JOOBLE_API_KEY to .env for "
                                     "country-wide results from many job boards."))
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
    results, kept_statuses = filter_jobs(found, query, country, start, end)
    statuses += kept_statuses
    order = {"JSearch": 0, "Adzuna": 1, "Jooble": 2, "The Muse": 3, "Himalayas": 4, "RemoteOK": 5, "Remotive": 6,
             "Jobicy": 7, "Arbeitnow": 8, "Company boards": 9}
    statuses.sort(key=lambda s: (not s.ok, order.get(s.name, 99)))
    return SearchResult(query=query, country=country.name, date_from=start, date_to=end,
                        results=results[:MAX_RESULTS], sources=statuses,
                        elsewhere=elsewhere_links(query, country, (date_to - date_from).days + 1))


def job_key(j: FoundJob) -> tuple[str, str]:
    """Two results are the same job when title and company match (across sources)."""
    return (re.sub(r"\W+", " ", j.title.lower()).strip(), re.sub(r"\W+", " ", j.company.lower()).strip())


def filter_jobs(found: dict[str, list[FoundJob]], query: str, country: Country, start: str,
                end: str) -> tuple[list[FoundJob], list[SourceStatus]]:
    """YOUR three filters (title, country, date range) and de-duplication across sources, newest first. Shared by
    the free sources and the Apify run, so both are judged the same way."""
    kept: dict[tuple[str, str], FoundJob] = {}
    statuses: list[SourceStatus] = []
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
            key = job_key(j)
            if key in kept:
                # keep the version with the most useful details (full description, direct employer link)
                old = kept[key]
                aggregators = ("Adzuna", "Jooble")
                if (old.description_is_excerpt and not j.description_is_excerpt) or (
                        old.source in aggregators and j.source not in aggregators):
                    kept[key] = j
                continue
            kept[key] = j
            n_kept += 1
        statuses.append(SourceStatus(name=name, ok=True, found=len(jobs), kept=n_kept))
    results = sorted(kept.values(), key=lambda j: (j.posted or "", j.title_match == "strong"), reverse=True)
    return results, statuses


def default_range(days: int = 7) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=days - 1), today
