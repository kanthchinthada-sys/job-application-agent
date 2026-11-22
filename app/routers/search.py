import sqlite3
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StringConstraints

from app import importers, job_intake, job_search, page_reader, repository
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


@router.post("", response_model=job_search.SearchResult, responses={422: {"description": "Bad filters"}})
def search(body: SearchRequest, request: Request, conn: Conn):
    """Find jobs by title, country and date range across free job sources. Nothing is saved."""
    code = body.country.upper()
    if code not in job_search.COUNTRIES:
        raise HTTPException(status_code=422, detail=f"Unknown country {body.country}.")
    if body.date_from > body.date_to:
        raise HTTPException(status_code=422, detail="The start date is after the end date.")
    if (body.date_to - body.date_from).days > 92:
        raise HTTPException(status_code=422, detail="Choose a date range of at most 3 months.")
    result = job_search.search(body.title, code, body.date_from, min(body.date_to, date.today()),
                               request.app.state.fetch, boards=_prefs_boards(conn),
                               request=getattr(request.app.state, "request_api", None))
    for j in result.results:
        row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (j.url,)).fetchone()
        j.already_added_job_id = row["id"] if row else None
    return result


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
            full = importers.find_on_company_board(company, title, request.app.state.fetch)
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
        info = job_intake.intake(description, url, request.app.state.llm, known_title=title,
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
