import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from app.dependencies import llm as account_llm

from pydantic import BaseModel

from app import ai_match, importers, job_intake, matching, page_reader, repository
from app.dependencies import get_connection, intake_worker
from app.errors import ConflictError
from app.schemas import JobCreate, JobOut, JobQuickAdd, JobUpdate, MatchReport

router = APIRouter(prefix="/jobs", tags=["jobs"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
JobId = Annotated[int, Path(gt=0)]


@router.post("", response_model=JobOut, status_code=201, responses={409: {"description": "Duplicate source_url"}})
def create_job(job: JobCreate, conn: Conn):
    """Add a job with every field given explicitly. Most people use POST /jobs/quick instead."""
    return repository.create_job(conn, job)


class QuickAddOut(BaseModel):
    job: JobOut
    intake: job_intake.Intake


def _normal_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    return url if "://" in url else "https://" + url


@router.post("/quick", response_model=QuickAddOut, status_code=201,
             responses={409: {"description": "Already added, or the link can't be read"},
                        422: {"description": "Neither a readable link nor a description"}})
def quick_add(body: JobQuickAdd, request: Request, conn: Conn):
    """Add a job from its link and/or description, without waiting for the model.

    Title, company and location come from (in order) what you typed, the job board's API (Greenhouse, Lever,
    Ashby), the page's structured data (schema.org JobPosting, or its title/site name), then rules. The job is
    saved at once; when something is still missing or uncertain the local AI is asked in the background
    (GET /jobs/{id}/intake shows how it went) and fills only the fields you haven't changed."""
    from fastapi import HTTPException
    url = _normal_url(body.url)
    description = body.description or ""
    known: dict[str, tuple[str, str]] = {}
    board = None
    if url:
        try:
            link = importers.parse_link(url)
            if link.job_id:
                board = importers.fetch_job(link, request.app.state.fetch)
        except ConflictError:
            board = None                     # not a supported board: the description is needed
    page, meta = None, {}
    if url and board is None:
        rules_unsure = bool(description.strip()) and bool(
            job_intake.read_with_rules(description, url).missing) and not (body.title and body.company)
        if len(description.strip()) < 800 or rules_unsure:
            # any other job page: its schema.org JobPosting (most job sites publish one), else its title/site name
            page, meta = page_reader.fetch_page_info(url, request.app.state.fetch_page)
            if page is not None and len(page.description) > len(description):
                description = page.description
    if board is not None:
        url = board.source_url
        if len(board.description) >= len(description):
            description = board.description
    if url:
        row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (url,)).fetchone()
        if row:
            raise ConflictError(f"This posting is already in your jobs (job id {row['id']}).", existing_id=row["id"])
    if not body.allow_duplicate and len(description.strip()) >= 50:
        # the same posting pasted again: found before any AI runs
        same = _same_description(conn, description)
        if same is not None:
            raise ConflictError(f"You already saved this posting: '{same['title']}' at {same['company']} "
                                f"(job id {same['id']}).", existing_id=same["id"])
    if len(description.strip()) < 50:
        raise HTTPException(status_code=422, detail="Paste the job description too: this page doesn't publish "
                                                    "its posting in a form the app can read.")
    # most reliable first: what you typed, the job board, the page's structured data, the page's title/site name
    for key in ("title", "company", "location"):
        if board is not None and getattr(board, key, None):
            known.setdefault(key, (getattr(board, key), "job board"))
        if page is not None and getattr(page, key, None):
            known.setdefault(key, (getattr(page, key), "job page"))
        if meta.get(key):
            known.setdefault(key, meta[key])
    for key in ("title", "company"):
        if getattr(body, key):
            known[key] = (getattr(body, key), "you")
    info = job_intake.read_with_rules(description, url, known)
    for key, (_, source) in known.items():
        if source == "job page (site name)":
            info.review.append(key)          # a careers site's name is usually, not always, the company
    from pydantic import ValidationError
    try:
        new = JobCreate(title=info.title, company=info.company, location=info.location, source_url=url,
                        description=description, required_skills=info.required_skills,
                        preferred_skills=info.preferred_skills, allow_duplicate=body.allow_duplicate)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Could not add this job: {exc.errors()[0]['msg']} "
                                                    f"({'.'.join(map(str, exc.errors()[0]['loc']))}).") from exc
    job = repository.create_job(conn, new)
    if info.missing:
        if body.use_ai and account_llm(request) is not None:
            info.ai_state = "pending"
            intake_worker(request).start(conn, job.id, info, description, url)
        else:
            info.ai_state = "off"
    return QuickAddOut(job=job, intake=info)


class IntakeStatus(BaseModel):
    ai_state: str                 # not_needed | pending | done | failed | off
    review: list[str] = []        # fields to check (missing, or found an uncertain way)
    notes: list[str] = []
    found_by: dict[str, str] = {}
    applied: list[str] = []       # fields the AI filled
    kept: list[str] = []          # fields the AI would have changed, kept as you corrected them
    attempts: list[dict] = []
    job: JobOut


@router.get("/{job_id}/intake", response_model=IntakeStatus, responses={404: {"description": "Job not found"}})
def intake_status(job_id: JobId, request: Request, conn: Conn):
    """How the model's background step for a new job went. Reading it never calls the model."""
    job = repository.get_job(conn, job_id)
    data = intake_worker(request).get(conn, job_id) or {"ai_state": "not_needed"}
    return IntakeStatus(job=job, **{k: v for k, v in data.items() if k in IntakeStatus.model_fields})


@router.patch("/{job_id}", response_model=JobOut, responses={404: {"description": "Job not found"}})
def update_job(job_id: JobId, body: JobUpdate, request: Request, conn: Conn):
    """Fix the title, company, location, description or skills that were read from the posting.

    A field you change here is never overwritten by the model's background step, even if it answers later.
    A new description without skills: the skills are read again from it (rules, plus the local AI if needed)."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    repository.get_job(conn, job_id)
    fields = {"skills" if k.endswith("_skills") else k for k in changes}
    intake_worker(request).lock(conn, job_id, fields)     # before saving: a late answer can't slip in between
    if "description" in changes and "required_skills" not in changes:
        job = repository.get_job(conn, job_id)
        info = job_intake.intake(changes["description"], job.source_url and str(job.source_url),
                                 account_llm(request), known_title=changes.get("title", job.title))
        changes["required_skills"], changes["preferred_skills"] = info.required_skills, info.preferred_skills
    return repository.update_job(conn, job_id, changes)


@router.post("/{job_id}/refresh", response_model=JobOut,
             responses={404: {"description": "Job not found"}, 409: {"description": "The page can't be read"}})
def refresh_job(job_id: JobId, request: Request, conn: Conn):
    """Read the full posting again from the job's link (its schema.org JobPosting, or the Greenhouse / Lever /
    Ashby API) and read the skills from it again. Use it when only a short excerpt was saved."""
    job = repository.get_job(conn, job_id)
    if not job.source_url:
        raise ConflictError("This job has no link to read. Paste the full description instead.")
    url = str(job.source_url)
    description = None
    try:
        link = importers.parse_link(url)
        if link.job_id:
            description = importers.fetch_job(link, request.app.state.fetch).description
    except ConflictError:
        pass
    if not description:
        page = page_reader.fetch_posting(url, request.app.state.fetch_page)
        description = page.description if page else None
    if not description or len(description) <= len(job.description):
        raise ConflictError("The full posting couldn't be read from its page. Open the posting, copy the "
                            "description and paste it instead.")
    info = job_intake.intake(description, url, account_llm(request), known_title=job.title)
    return repository.update_job(conn, job_id, {"description": description, "required_skills": info.required_skills,
                                                "preferred_skills": info.preferred_skills})


@router.get("", response_model=list[JobOut])
def list_jobs(conn: Conn):
    return repository.list_jobs(conn)


@router.get("/{job_id}", response_model=JobOut, responses={404: {"description": "Job not found"}})
def get_job(job_id: JobId, conn: Conn):
    return repository.get_job(conn, job_id)


@router.get(
    "/{job_id}/match",
    response_model=MatchReport,
    responses={404: {"description": "Job or profile not found"}},
)
def match_job(job_id: JobId, conn: Conn):
    """Compare the job's listed skills with your profile (rule-based evidence comparison, no AI).

    Read `method_label` and `limitations` in the response before relying on it.
    """
    job = repository.get_job(conn, job_id)
    profile = repository.get_profile(conn)
    return MatchReport(
        job_id=job.id,
        method=matching.METHOD,
        method_label=matching.METHOD_LABEL,
        required=matching.compare_skills(profile, job.required_skills),
        preferred=matching.compare_skills(profile, job.preferred_skills),
        limitations=matching.LIMITATIONS,
        profile_updated_at=profile.updated_at,
    )


@router.post(
    "/{job_id}/match/ai",
    response_model=ai_match.AIMatchReport,
    responses={404: {"description": "Job or profile not found"}, 503: {"description": "Ollama not running"}},
)
def ai_second_look(job_id: JobId, conn: Conn, request: Request):
    """Ask the local AI to look for related experience behind the skills the rule-based match did not find.

    Every suggestion quotes your own profile (checked by code). Suggestions do not change the match score.
    """
    job = repository.get_job(conn, job_id)
    profile = repository.get_profile(conn)
    missing = (matching.compare_skills(profile, job.required_skills).missing
               + matching.compare_skills(profile, job.preferred_skills).missing)
    return ai_match.second_look(account_llm(request), profile, missing)


def _same_description(conn, description: str):
    import re
    norm = lambda t: re.sub(r"\W+", " ", t).strip().lower()
    target = norm(description)
    for row in conn.execute("SELECT id, title, company, description FROM jobs"):
        if norm(row["description"]) == target:
            return row
    return None


@router.get("/{job_id}/requirements", responses={404: {"description": "Job or profile not found"}})
def requirements(job_id: JobId, conn: Conn):
    """The posting's requirements and, for each, your evidence: the exact employer/project and experience
    records behind a direct match, related experience, or a gap. The same analysis the resume uses; no AI."""
    from app import selection
    from app.bank import verified
    profile = repository.get_profile(conn)
    job = repository.get_job(conn, job_id)
    plan = selection.plan(profile, job)
    records = {r.id: r for r in profile.experience_bank}
    label = {"direct": "direct evidence", "partial": "direct evidence (part of it)", "transferable": "related experience",
             "skills only": "related experience (skills list)", "gap": "gap", "shown by example": "shown by example",
             "not shown": "not shown"}
    rows = []
    for r in plan.rows:
        rows.append({
            "requirement": r.requirement, "kind": r.kind, "category": r.category, "strength": r.strength,
            "evidence": label.get(r.strength, r.strength), "found": r.found_parts, "missing": r.not_found_parts,
            "found_elsewhere": r.found_elsewhere,
            "records": [{"id": i, "owner": records[i].owner, "text": records[i].text} for i in r.selected_ids
                        if i in records],
        })
    order = {"required": 0, "preferred": 1, "responsibility": 2}
    cat = {"technical": 0, "education": 1, "soft skill": 2}
    rows.sort(key=lambda x: (cat.get(x["category"], 3), order.get(x["kind"], 3)))
    return {"job_id": job.id, "role_types": plan.role_types, "has_bank": bool(verified(profile)),
            "eligibility": plan.eligibility, "rows": rows}
