import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app import repository
from app.dependencies import get_connection
from app.schemas import ApplicationCreate, ApplicationOut, ApplicationStatus, ApplicationUpdate

router = APIRouter(prefix="/applications", tags=["applications"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
AppId = Annotated[int, Path(gt=0)]


@router.post(
    "",
    response_model=ApplicationOut,
    status_code=201,
    responses={404: {"description": "Job not found"}, 409: {"description": "Application already exists for this job"}},
)
def create_application(data: ApplicationCreate, conn: Conn):
    """Start tracking an application for a job. One application per job."""
    return repository.create_application(conn, data)


@router.get("", response_model=list[ApplicationOut])
def list_applications(conn: Conn, status: ApplicationStatus | None = None):
    """List applications, optionally filtered by `status`."""
    return repository.list_applications(conn, status)


@router.get("/{application_id}", response_model=ApplicationOut, responses={404: {"description": "Not found"}})
def get_application(application_id: AppId, conn: Conn):
    return repository.get_application(conn, application_id)


@router.patch(
    "/{application_id}",
    response_model=ApplicationOut,
    responses={404: {"description": "Not found"}, 409: {"description": "Rule violation, e.g. confirmation missing"}},
)
def update_application(application_id: AppId, patch: ApplicationUpdate, conn: Conn):
    """Change status and/or notes. Status changes are recorded in `history`.

    `submitted_confirmed` requires `submission_confirmation` evidence.
    """
    return repository.update_application(conn, application_id, patch)
