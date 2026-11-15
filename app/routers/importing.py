import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, StringConstraints

from app import importers, preferences, repository
from app.dependencies import get_connection
from app.errors import NotFoundError
from app.schemas import JobPreferences, StrictModel

router = APIRouter(prefix="/import", tags=["import"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=500)]


class LinkRequest(StrictModel):
    url: Url


class BoardRequest(StrictModel):
    url: Url
    only_suitable: bool = True


class ImportPreview(BaseModel):
    job: importers.ImportedJob
    preferences: preferences.PreferenceCheck
    already_added_job_id: int | None


class BoardResult(BaseModel):
    posting: importers.BoardPosting
    preferences: preferences.PreferenceCheck
    already_added_job_id: int | None


class BoardSearch(BaseModel):
    board: str
    provider: str
    total_postings: int
    shown: int
    results: list[BoardResult]


def _prefs(conn) -> JobPreferences:
    try:
        return repository.get_profile(conn).preferences
    except NotFoundError:
        return JobPreferences()


def _existing_job_id(conn, url: str) -> int | None:
    row = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (url,)).fetchone()
    return row["id"] if row else None


@router.post("/job", response_model=ImportPreview, responses={404: {"description": "Job not found"},
                                                              409: {"description": "Unsupported link"}})
def preview_job(body: LinkRequest, request: Request, conn: Conn):
    """Read one posting from a Greenhouse, Lever or Ashby link. Nothing is saved.

    Review it, then save with POST /jobs (skills can be found with POST /ai/extract-skills).
    """
    job = importers.fetch_job(importers.parse_link(body.url), request.app.state.fetch)
    return ImportPreview(
        job=job,
        preferences=preferences.check(job.title, job.location, _prefs(conn)),
        already_added_job_id=_existing_job_id(conn, job.source_url),
    )


@router.post("/board", response_model=BoardSearch, responses={409: {"description": "Unsupported link"}})
def search_board(body: BoardRequest, request: Request, conn: Conn):
    """List a company's open postings, checked against your target roles and locations."""
    link = importers.parse_link(body.url)
    postings = importers.list_board(link, request.app.state.fetch)
    prefs = _prefs(conn)
    results = []
    for p in postings:
        check = preferences.check(p.title, p.location, prefs)
        if body.only_suitable and not check.suitable:
            continue
        results.append(BoardResult(posting=p, preferences=check,
                                   already_added_job_id=_existing_job_id(conn, p.source_url)))
    order = {"strong": 0, "partial": 1, "none": 2}
    results.sort(key=lambda r: (order[r.preferences.title_match], r.preferences.location_match != "yes"))
    return BoardSearch(board=link.board, provider=link.provider, total_postings=len(postings),
                       shown=len(results), results=results)
