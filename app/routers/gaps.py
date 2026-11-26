"""Review suggested technical evidence and regenerate the associated job's resume on confirmation."""

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StringConstraints

from app import gap_review
from app.dependencies import get_connection, preparer
from app.errors import NotFoundError
from app.schemas import StrictModel

router = APIRouter(tags=["experience bank"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]


class ConfirmGap(StrictModel):
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=600)]
    confirmed: Literal[True]


@router.get("/experience-gaps")
def list_gaps(conn: Conn, job_id: int | None = None):
    if job_id is not None:
        from app import repository
        gap_review.ensure(conn, repository.get_job(conn, job_id))
    return gap_review.list_suggestions(conn, job_id)


@router.post("/experience-gaps/{suggestion_id}/draft")
def draft_gap(suggestion_id: int, request: Request, conn: Conn, again: bool = False):
    """The AI writes the line for this gap, at the employer or project where the skill fits best. Nothing is
    added to your bank until you confirm it is true."""
    from app.dependencies import llm as account_llm
    return gap_review.draft(conn, account_llm(request), suggestion_id, again)


@router.post("/experience-gaps/{suggestion_id}/confirm")
def confirm_gap(suggestion_id: int, body: ConfirmGap, request: Request, conn: Conn):
    record_id, application_id = gap_review.confirm(conn, suggestion_id, body.owner, body.text)
    return {"record_id": record_id, "application_id": application_id,
            "preparation": preparer(request).refresh_resume(application_id)}


class ConfirmItem(StrictModel):
    id: int
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=600)]


class ConfirmGaps(StrictModel):
    items: Annotated[list[ConfirmItem], Field(min_length=1, max_length=50)]
    confirmed: Literal[True]


@router.post("/experience-gaps/confirm")
def confirm_gaps(body: ConfirmGaps, request: Request, conn: Conn):
    """Add every line you confirmed as true in one go: each job's resume is then written once, for all of them."""
    records, apps = gap_review.confirm_many(conn, [(i.id, i.owner, i.text) for i in body.items])
    prep = preparer(request)
    return {"record_ids": records, "application_ids": apps,
            "preparations": [prep.refresh_resume(aid) for aid in apps]}


@router.delete("/experience-gaps/{suggestion_id}")
def dismiss_gap(suggestion_id: int, conn: Conn):
    if not conn.execute("UPDATE gap_suggestions SET state='dismissed' WHERE id=? AND state='pending'",
                        (suggestion_id,)).rowcount:
        raise NotFoundError("That technical gap is no longer on your review list.")
    conn.commit()
    return {"dismissed": True}
