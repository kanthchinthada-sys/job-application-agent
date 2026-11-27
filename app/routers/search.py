import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from app.dependencies import llm as account_llm
from pydantic import BaseModel, StringConstraints

from app import apify_search, importers, job_intake, job_search, page_reader, repository
from app.dependencies import get_connection
from app.errors import ConflictError, NotFoundError
from app.schemas import ApplicationCreate, JobCreate, StrictModel

router = APIRouter(prefix="/search", tags=["find jobs"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]


class CountryOut(BaseModel):
    code: str
    name: str
    adzuna: bool                     # country-wide Adzuna results available (with a key)


class CountriesOut(BaseModel):
    countries: list[CountryOut]
    adzuna_configured: bool


class SearchRequest(StrictModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    country: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2)]
    date_from: date
    date_to: date


class SaveSearch(StrictModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    filters: SearchRequest


class SavedSearchPreset(BaseModel):
    id: int
    name: str
    filters: SearchRequest
    last_run_at: str | None


class SavedSearch(BaseModel):
    filters: SearchRequest
    result: job_search.SearchResult
    applications: dict[str, int]


class ApplyRequest(StrictModel):
    job: job_search.FoundJob


class ApplyOut(BaseModel):
    job_id: int
    application_id: int
    preparation: str = "idle"        # queued / running / done: the resume and cover letter are made in the background
    apply_url: str
    new_job: bool
    new_application: bool
    notes: list[str]


@router.get("/countries", response_model=CountriesOut)
def countries():
    """The countries Find jobs can search, and whether the (optional) Adzuna key is set."""
    return CountriesOut(
        countries=[CountryOut(code=c.code, name=c.name, adzuna=bool(c.adzuna)) for c in job_search.COUNTRIES.values()],
        adzuna_configured=job_search.adzuna_configured())


def _prefs_boards(conn) -> list[str]:
    try:
        return repository.get_profile(conn).preferences.company_boards
    except NotFoundError:
        return []


@router.get("/latest", response_model=SavedSearch | None)
def latest_search(conn: Conn):
    """Return this workspace's results without calling any job source again."""
    row = conn.execute("SELECT filters, result FROM last_job_search WHERE id = 1").fetchone()
    if row is None:
        return None
    result = job_search.SearchResult.model_validate_json(row["result"])
    applications = {}
    for job in result.results:
        saved = conn.execute(
            """SELECT jobs.id, applications.id AS application_id FROM jobs
               LEFT JOIN applications ON applications.job_id = jobs.id
               WHERE source_url = ? OR (title = ? COLLATE NOCASE AND company = ? COLLATE NOCASE)
               ORDER BY source_url = ? DESC LIMIT 1""",
            (job.url, job.title, job.company, job.url),
        ).fetchone()
        if saved:
            job.already_added_job_id = saved["id"]
            if saved["application_id"]:
                applications[job.url] = saved["application_id"]
    return SavedSearch(filters=SearchRequest.model_validate_json(row["filters"]), result=result,
                       applications=applications)


@router.post("", response_model=job_search.SearchResult, responses={422: {"description": "Bad filters"}})
def search(body: SearchRequest, request: Request, conn: Conn):
    """Find jobs across free sources and remember the results for this workspace."""
    result = _run_search(body, request, conn)
    _remember_search(conn, body, result)
    return result


def _run_search(body: SearchRequest, request: Request, conn: sqlite3.Connection):
    code = _check_filters(body)
    result = job_search.search(body.title, code, body.date_from, min(body.date_to, date.today()),
                               request.app.state.fetch, boards=_prefs_boards(conn),
                               request=getattr(request.app.state, "request_api", None))
    from app import bank, core_skills, evidence
    try:
        profile = repository.get_profile(conn)
    except NotFoundError:
        profile = None
    stated = []
    if profile is not None:
        terms = profile.skills + [s for g in profile.skill_groups for s in g.skills]
        terms += [s for item in [*profile.experience, *profile.projects] for s in [*item.skills, *item.highlights]]
        terms += [r.text for r in bank.verified(profile)]
        stated = [(str(i), s) for i, s in enumerate(terms)]
    for j in result.results:
        row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (j.url,)).fetchone()
        j.already_added_job_id = row["id"] if row else None
        location = f"{j.location} {j.where}"
        workplace = re.search(r"\b(hybrid|on[- ]site|in[- ]office|fully remote)\b", location, re.IGNORECASE)
        if workplace:
            mode = workplace[1].casefold()
            j.workplace = "hybrid" if mode == "hybrid" else "remote" if mode == "fully remote" else "on-site"
        elif j.remote:
            j.workplace = "remote"
        if j.description:
            salary = re.search(
                r"(?:salary|base pay|compensation|pay range)[: \t]{0,12}([^\n.]{0,130}[$€£]\s*\d[^\n.]{0,100})",
                j.description, re.IGNORECASE,
            )
            j.salary_text = salary[1].strip() if salary else None
        if not stated or not j.description:
            continue
        info = job_intake.read_with_rules(j.description, j.url)
        ask = list(dict.fromkeys(s for s in info.required_skills if core_skills.is_listable(s)))
        for skill in ask:
            hit = evidence.find_part(skill, stated)
            if hit and not hit.related:
                j.technical_matches.append(skill)
            elif not j.description_is_excerpt:
                j.technical_gaps.append(skill)
    return result


def _remember_search(conn, body, result):
    conn.execute("INSERT OR REPLACE INTO last_job_search (id, filters, result) VALUES (1, ?, ?)",
                 (body.model_copy(update={"country": body.country.upper()}).model_dump_json(), result.model_dump_json()))
    conn.commit()


def _preset(row):
    return SavedSearchPreset(id=row["id"], name=row["name"], filters=SearchRequest.model_validate_json(row["filters"]),
                             last_run_at=row["last_run_at"])


@router.get("/saved", response_model=list[SavedSearchPreset])
def saved_searches(conn: Conn):
    return [_preset(row) for row in conn.execute("SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE")]


@router.post("/saved", response_model=SavedSearchPreset, status_code=201)
def save_search(body: SaveSearch, conn: Conn):
    _check_filters(body.filters)
    filters = body.filters.model_dump_json()
    conn.execute(
        """INSERT INTO saved_searches (name, filters) VALUES (?, ?)
           ON CONFLICT(name) DO UPDATE SET filters=excluded.filters,
             seen=CASE WHEN filters=excluded.filters THEN seen ELSE '[]' END,
             last_run_at=CASE WHEN filters=excluded.filters THEN last_run_at ELSE NULL END""",
        (body.name, filters),
    )
    conn.commit()
    return _preset(conn.execute("SELECT * FROM saved_searches WHERE name=? COLLATE NOCASE", (body.name,)).fetchone())


@router.post("/saved/{search_id}/run", response_model=job_search.SearchResult)
def run_saved_search(search_id: int, request: Request, conn: Conn):
    row = conn.execute("SELECT * FROM saved_searches WHERE id=?", (search_id,)).fetchone()
    if not row:
        raise NotFoundError("Saved search not found.")
    filters = SearchRequest.model_validate_json(row["filters"])
    today = date.today()
    filters = filters.model_copy(update={"date_from": today - (filters.date_to - filters.date_from), "date_to": today})
    result = _run_search(filters, request, conn)
    conn.execute("BEGIN IMMEDIATE")
    latest = conn.execute("SELECT seen, last_run_at, filters FROM saved_searches WHERE id=?", (search_id,)).fetchone()
    if not latest:
        raise NotFoundError("Saved search was removed while it was running.")
    if latest["filters"] != row["filters"]:
        raise ConflictError("The saved filters changed while the search was running. Run the updated search again.")
    seen = json.loads(latest["seen"])
    lookup = set(seen)
    for job in result.results:
        job.new_since_last_run = bool(latest["last_run_at"] and job.url not in lookup)
    if any(source.ok for source in result.sources):
        seen = list(dict.fromkeys([*seen, *(job.url for job in result.results)]))[-3000:]
        conn.execute("UPDATE saved_searches SET seen=?, last_run_at=? WHERE id=?",
                     (json.dumps(seen), datetime.now(UTC).isoformat(), search_id))
    _remember_search(conn, filters, result)
    return result


@router.delete("/saved/{search_id}")
def delete_saved_search(search_id: int, conn: Conn):
    if not conn.execute("DELETE FROM saved_searches WHERE id=?", (search_id,)).rowcount:
        raise NotFoundError("Saved search not found.")
    conn.commit()
    return {"deleted": True}


# --- Apify (optional, paid): structured listings from employer career sites, run in the background -----------

def _check_filters(body: SearchRequest) -> str:
    code = body.country.upper()
    if code not in job_search.COUNTRIES:
        raise HTTPException(status_code=422, detail=f"Unknown country {body.country}.")
    if body.date_from > body.date_to:
        raise HTTPException(status_code=422, detail="The start date is after the end date.")
    if (body.date_to - body.date_from).days > 92:
        raise HTTPException(status_code=422, detail="Choose a date range of at most 3 months.")
    return code


def _apify_request(request: Request):
    return getattr(request.app.state, "apify_request", None) or apify_search.http


def _already_added(conn):
    def find(url: str):
        row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (url,)).fetchone()
        return row["id"] if row else None
    return find


@router.get("/apify")
def apify_settings(request: Request, conn: Conn):
    """Whether Apify is set up, the limits the next run uses, and your latest run (its progress or results).
    The token is never returned."""
    latest = apify_search.latest(conn)
    run = None
    if latest is not None:
        try:
            run = apify_search.refresh(conn, latest, _apify_request(request), _already_added(conn))
        except apify_search.ApifyError as exc:
            run = {"id": latest, "status": "ERROR", "message": str(exc), "results": [], "active": False}
    return {"settings": apify_search.settings(), "latest": run}


@router.post("/apify", responses={422: {"description": "Bad filters"}, 503: {"description": "Apify not set up"}})
def apify_start(body: SearchRequest, request: Request, conn: Conn):
    """Start a paid Apify search with the same filters as Find jobs (title, country, dates). Returns at once; ask
    GET /search/apify/{id} for progress. While a run is active, this returns that run instead of starting another."""
    code = _check_filters(body)
    if not apify_search.settings()["configured"]:
        raise HTTPException(status_code=503, detail="Apify is not set up: add APIFY_API_TOKEN to .env and restart "
                                                    "the app.")
    try:
        run = apify_search.start_run(conn, body.title, code, body.date_from, min(body.date_to, date.today()),
                                     _apify_request(request))
    except apify_search.ApifyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return apify_search.refresh(conn, run["id"], _apify_request(request), _already_added(conn)) | {
        "reused": run.get("reused", "")}


@router.get("/apify/{run_id}")
def apify_progress(run_id: int, request: Request, conn: Conn):
    """A run's progress; once it has finished, its results (filtered and de-duplicated like Find jobs)."""
    try:
        return apify_search.refresh(conn, run_id, _apify_request(request), _already_added(conn))
    except LookupError:
        raise HTTPException(status_code=404, detail="No such Apify search in your workspace.") from None


@router.post("/apply", response_model=ApplyOut)
def apply(body: ApplyRequest, request: Request, conn: Conn):
    """Save a found job and start its application, ready for the resume and cover letter.

    Nothing is submitted: you apply on the employer's page yourself."""
    found = body.job
    notes: list[str] = []
    url = found.url
    new_job = False
    row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (url,)).fetchone()
    if row:
        job_id = row["id"]
    else:
        description = found.description or ""
        title, company, location = found.title, found.company, found.where or found.location
        try:
            link = importers.parse_link(url)
            if link.job_id:                                   # a Greenhouse / Lever / Ashby job: read it in full
                board = importers.fetch_job(link, request.app.state.fetch)
                description, location = board.description or description, board.location or location
                url = board.source_url or url
        except (ConflictError, NotFoundError):
            pass
        if len(description) < 800 or found.description_is_excerpt:
            # only an excerpt (e.g. Adzuna): read the posting's own page (schema.org JobPosting) ...
            page = page_reader.fetch_posting(found.url, request.app.state.fetch_page)
            if page is not None and len(page.description) > len(description):
                description = page.description
                location = location or page.location
                found = found.model_copy(update={"description_is_excerpt": False})
        if len(description) < 800 or found.description_is_excerpt:
            # ... or look for the same job on the company's own careers board
            full = importers.find_on_company_board(company, title, request.app.state.fetch, location=location or "")
            if full is not None and len(full.description) > len(description):
                description, url = full.description, full.source_url or url
                location = full.location or location
                found = found.model_copy(update={"description_is_excerpt": False})
                notes.append(f"Full posting found on {company}'s careers page; Apply there: {url}")
        if len(description) < 800 or found.description_is_excerpt:
            notes.append("Only a short excerpt of the description was available: paste the full description in "
                         "Materials for a resume tailored to the real requirements.")
        if len(description.strip()) < 20:
            description = f"{title} at {company} ({location}). Full description: {url}"
        # title and company come from the search result: the model is asked only if the skills are thin
        info = job_intake.intake(description, url, account_llm(request), known_title=title,
                                 known={"company": (company, "job search")} if company else None)
        notes += [n for n in info.notes if "No job title" not in n and "No company" not in n]
        try:
            job = repository.create_job(conn, JobCreate(
                title=title, company=company, location=location or None, source_url=url, description=description,
                required_skills=info.required_skills, preferred_skills=info.preferred_skills))
            job_id, new_job = job.id, True
        except ConflictError as exc:                          # the same title + company is already saved
            if exc.existing_id is None:
                raise
            job_id = exc.existing_id
            notes.append("You already had this job saved; using it.")
    try:
        app_out = repository.create_application(conn, ApplicationCreate(job_id=job_id))
        app_id, new_app = app_out.id, True
    except ConflictError as exc:
        if exc.existing_id is None:
            raise
        app_id, new_app = exc.existing_id, False
    # the resume and cover letter are made in the background, unless this application already has both
    kinds = {r["kind"] for r in conn.execute("SELECT DISTINCT kind FROM documents WHERE application_id = ?",
                                             (app_id,)).fetchall()}
    if {"resume", "cover_letter"} <= kinds:
        prep = "done"
    else:
        from app.dependencies import preparer
        prep = preparer(request).start(app_id).state
    return ApplyOut(job_id=job_id, application_id=app_id, apply_url=url, new_job=new_job,
                    new_application=new_app, notes=notes, preparation=prep)
