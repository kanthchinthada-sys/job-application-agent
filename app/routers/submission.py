"""Assisted apply (Greenhouse): plan -> your review and approval -> filled by the script -> you submit."""

import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse

from app import repository, resume_render, submission
from app.dependencies import get_connection
from app.errors import ConflictError, NotFoundError
from app.schemas import ApplicationOut, ApplicationStatus, ApplicationUpdate, DocumentOut

router = APIRouter(tags=["assisted apply"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Id = Annotated[int, Path(gt=0)]


def _latest(conn, application_id: int, kind: str) -> DocumentOut | None:
    docs = [d for d in repository.list_documents(conn, application_id) if d.kind == kind]
    return max(docs, key=lambda d: d.version) if docs else None


def _latest_plan(conn, application_id: int) -> tuple[DocumentOut, submission.SubmissionPlan]:
    doc = _latest(conn, application_id, "application_plan")
    if doc is None:
        raise NotFoundError("No application plan yet. Create one first.")
    return doc, submission.SubmissionPlan.model_validate_json(doc.content)


def _save_plan(conn, application_id: int, plan: submission.SubmissionPlan) -> DocumentOut:
    return repository.add_document(conn, application_id, "application_plan", "", plan.model_dump_json(),
                                   "agent", None, {"method": submission.METHOD, "warnings": plan.warnings,
                                                   "facts_used": [], "questions_for_you": []})


@router.post("/applications/{application_id}/submission/plan", response_model=DocumentOut, status_code=201,
             responses={404: {"description": "Application, job, or profile not found"},
                        409: {"description": "Not a Greenhouse job, or already submitted"}})
def create_plan(application_id: Id, request: Request, conn: Conn):
    """Read the job's real Greenhouse questions and propose answers from your profile. Nothing is submitted."""
    app = repository.get_application(conn, application_id)
    submission.guard_not_submitted(app)
    job = repository.get_job(conn, app.job_id)
    profile = repository.get_profile(conn)
    board, job_id = submission.greenhouse_ids(job)
    gh = submission.fetch_questions(board, job_id, request.app.state.fetch)
    screening = {d.question: d.content for d in repository.list_documents(conn, application_id)
                 if d.kind == "screening_answer" and d.content}
    return _save_plan(conn, application_id, submission.build_plan(profile, job, gh, screening))


@router.get("/applications/{application_id}/submission/plan", response_model=DocumentOut)
def get_plan(application_id: Id, conn: Conn):
    doc, _ = _latest_plan(conn, application_id)
    return doc


@router.post("/applications/{application_id}/submission/approve", response_model=DocumentOut,
             responses={409: {"description": "Required answers missing, or already submitted"}})
def approve_plan(application_id: Id, edit: submission.PlanEdit, conn: Conn):
    """Save your answers and approve the plan. Moves the application to 'ready for review'."""
    app = repository.get_application(conn, application_id)
    submission.guard_not_submitted(app)
    _, plan = _latest_plan(conn, application_id)
    plan = submission.apply_edits(plan, edit)
    submission.validate_approval(plan)
    plan.approved = True
    doc = _save_plan(conn, application_id, plan)
    if app.status is ApplicationStatus.preparing:
        repository.update_application(conn, application_id, ApplicationUpdate(
            status=ApplicationStatus.ready_for_review, event_note="Assisted-apply plan approved"))
    return doc


@router.get("/applications/{application_id}/submission/ready")
def ready_to_fill(application_id: Id, conn: Conn):
    """Used by the fill script: the approved plan, after the duplicate check."""
    app = repository.get_application(conn, application_id)
    submission.guard_not_submitted(app)
    _, plan = _latest_plan(conn, application_id)
    if not plan.approved:
        raise ConflictError("The latest plan isn't approved yet. Review and approve it in the app first.")
    return {"application_id": app.id, "job_title": app.job_title, "company": app.company,
            "has_resume": _latest(conn, application_id, "resume") is not None,
            "has_cover_letter": _latest(conn, application_id, "cover_letter") is not None,
            "plan": json.loads(plan.model_dump_json())}


@router.post("/applications/{application_id}/submission/result", response_model=ApplicationOut,
             responses={409: {"description": "Invalid outcome for the current state"}})
def record_result(application_id: Id, report: submission.OutcomeReport, conn: Conn):
    """What happened in the browser. Only a seen confirmation becomes 'submitted_confirmed'."""
    app = repository.get_application(conn, application_id)
    status = submission.outcome_to_status(report.outcome)
    note = f"Assisted apply: {report.outcome}" + (f" ({report.details})" if report.details else "")
    if status is None:
        repository.add_note(conn, application_id, note)
        return repository.get_application(conn, application_id)
    if report.outcome == "confirmed" and not report.evidence:
        raise ConflictError("A confirmed submission needs evidence (the confirmation page).")
    if app.status is not ApplicationStatus.submission_attempted and status is not app.status:
        repository.update_application(conn, application_id, ApplicationUpdate(
            status=ApplicationStatus.submission_attempted, event_note="Submit clicked in the browser"))
    return repository.update_application(conn, application_id, ApplicationUpdate(
        status=status, submission_confirmation=report.evidence or None, event_note=note))


@router.get("/applications/{application_id}/cover-letter", response_class=HTMLResponse,
            responses={404: {"description": "No cover letter yet"}})
def latest_cover_letter(application_id: Id, conn: Conn):
    """Printable page of the latest cover letter (the fill script saves it as a PDF)."""
    doc = _latest(conn, application_id, "cover_letter")
    if doc is None:
        raise NotFoundError("No cover letter yet for this application.")
    app = repository.get_application(conn, application_id)
    profile = repository.get_profile(conn)
    contact = profile.contact.model_dump(mode="json")
    return resume_render.render_letter(doc.content, profile.full_name, contact, app.job_title, app.company)
