"""Assisted apply (Greenhouse): plan -> your review and approval -> filled by the script -> you submit."""

import json
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, StringConstraints

from app import auto_apply, repository, resume_render, submission
from app.dependencies import db_path, get_connection
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
    run = auto_apply.assert_no_active_run(conn, application_id)
    app = repository.get_application(conn, application_id)
    if run.state in {"uncertain", "already_applied", "confirmed"}:
        raise ConflictError("Check the previous outcome in Auto-apply before opening the form again.")
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
    auto_apply.assert_no_active_run(conn, application_id)
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


def _local_browser(request: Request) -> bool:
    if request.app.state.test_career_browser and request.client and request.client.host == "testclient":
        return True
    if not request.app.state.local_browser or not request.client:
        return False
    loopback = {"localhost", "127.0.0.1", "::1"}
    origin = request.headers.get("origin")
    return (request.client.host in loopback and request.url.hostname in loopback
            and (not origin or origin == f"{request.url.scheme}://{request.url.netloc}"))


@router.get("/applications/{application_id}/auto-apply")
def automatic_setup(application_id: Id, request: Request, conn: Conn):
    if not _local_browser(request):
        return {"supported": False, "reason": "Auto-apply is available from the app on your local computer."}
    return auto_apply.setup(conn, application_id)


class PortalLink(BaseModel):
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@router.put("/applications/{application_id}/auto-apply/portal")
def automatic_portal(application_id: Id, body: PortalLink, request: Request, conn: Conn):
    if not _local_browser(request):
        raise HTTPException(403, "Auto-apply is available from the app on your local computer.")
    return auto_apply.save_portal(conn, application_id, body.url)


@router.post("/applications/{application_id}/auto-apply", response_model=auto_apply.Run, status_code=202)
def automatic_submit(application_id: Id, body: auto_apply.Approval, request: Request, conn: Conn):
    """Start or continue the approved local browser run for this exact application and document versions."""
    if not _local_browser(request):
        raise HTTPException(403, "Auto-apply is available from the app on your local computer.")
    run, token = auto_apply.enqueue(conn, application_id, body)
    if token:
        request.app.state.auto_apply_workers.submit(db_path(request), application_id, token)
    return run


@router.post("/applications/{application_id}/auto-apply/cancel", response_model=auto_apply.Run)
def automatic_cancel(application_id: Id, request: Request, conn: Conn):
    if not _local_browser(request):
        raise HTTPException(403, "Auto-apply is available from the app on your local computer.")
    return auto_apply.cancel(conn, application_id)


class SubmissionReviewed(BaseModel):
    verified_not_received: Literal[True]


@router.post("/applications/{application_id}/auto-apply/review", response_model=auto_apply.Run)
def automatic_review(application_id: Id, body: SubmissionReviewed, request: Request, conn: Conn):
    if not _local_browser(request):
        raise HTTPException(403, "Auto-apply is available from the app on your local computer.")
    return auto_apply.clear_unreceived(conn, application_id)


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
