import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from pydantic import BaseModel

from app import ai_match, importers, job_intake, matching, repository
from app.dependencies import get_connection
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
    """Add a job from its link and/or description. Title, company, location and skills are read from the posting
    (job-board API for Greenhouse/Lever/Ashby links, otherwise rules plus the local AI, checked against the text)."""
    from fastapi import HTTPException
    url = _normal_url(body.url)
    description = body.description or ""
    board = None
    if url:
        try:
            link = importers.parse_link(url)
            if link.job_id:
                board = importers.fetch_job(link, request.app.state.fetch)
        except ConflictError:
            board = None                     # not a supported board: the description is needed
    if board is not None:
        url = board.source_url
        if len(board.description) >= len(description):
            description = board.description
    if url:
        row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (url,)).fetchone()
        if row:
            raise ConflictError(f"This posting is already in your jobs (job id {row['id']}).", existing_id=row["id"])
    if len(description.strip()) < 50:
        raise HTTPException(status_code=422, detail="Paste the job description too: this link can't be read "
                                                    "automatically (only Greenhouse, Lever and Ashby links can).")
    info = job_intake.intake(description, url, request.app.state.llm, use_ai=body.use_ai)
    if board is not None:
        info.title, info.company = board.title, board.company
        info.location = board.location or info.location
        info.found_by.update({"title": "job board", "company": "job board", "location": "job board"})
    from pydantic import ValidationError
    try:
        new = JobCreate(title=info.title, company=info.company, location=info.location, source_url=url,
                        description=description, required_skills=info.required_skills,
                        preferred_skills=info.preferred_skills, allow_duplicate=body.allow_duplicate)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Could not add this job: {exc.errors()[0]['msg']} "
                                                    f"({'.'.join(map(str, exc.errors()[0]['loc']))}).") from exc
    job = repository.create_job(conn, new)
    return QuickAddOut(job=job, intake=info)


@router.patch("/{job_id}", response_model=JobOut, responses={404: {"description": "Job not found"}})
def update_job(job_id: JobId, body: JobUpdate, conn: Conn):
    """Fix the title, company, location or skills that were read from the posting."""
    return repository.update_job(conn, job_id, body.model_dump(exclude_unset=True, exclude_none=True))


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
    return ai_match.second_look(request.app.state.llm, profile, missing)
