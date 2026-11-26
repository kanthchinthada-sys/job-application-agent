"""Review suggested technical evidence and regenerate the associated job's resume on confirmation."""

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import StringConstraints

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


@router.post("/experience-gaps/{suggestion_id}/confirm")
def confirm_gap(suggestion_id: int, body: ConfirmGap, request: Request, conn: Conn):
    record_id, application_id = gap_review.confirm(conn, suggestion_id, body.owner, body.text)
    return {"record_id": record_id, "application_id": application_id,
            "preparation": preparer(request).refresh_resume(application_id)}


@router.delete("/experience-gaps/{suggestion_id}")
def dismiss_gap(suggestion_id: int, conn: Conn):
    if not conn.execute("UPDATE gap_suggestions SET state='dismissed' WHERE id=? AND state='pending'",
                        (suggestion_id,)).rowcount:
        raise NotFoundError("That technical gap is no longer on your review list.")
    conn.commit()
    return {"dismissed": True}
