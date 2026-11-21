import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from app import drafting, repository, resume_render, tailoring
from app.errors import ConflictError
from app.dependencies import get_connection
from app.preparation import Preparation
from app.schemas import DocumentEdit, DocumentOut, DraftRequest

router = APIRouter(tags=["materials"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Id = Annotated[int, Path(gt=0)]


@router.post(
    "/applications/{application_id}/drafts",
    response_model=DocumentOut,
    status_code=201,
    responses={404: {"description": "Application or profile not found"}, 503: {"description": "Ollama not available"}},
)
def create_draft(application_id: Id, body: DraftRequest, request: Request, conn: Conn):
    """Draft a cover letter, screening answer, or tailored resume from your verified profile (AI, local).

    Saved as a new version. Read `review.warnings` and `review.questions_for_you` before using it.
    Takes roughly 20-120 seconds on a laptop. Resumes are opened with GET /documents/{id}/print.
    """
    return make_draft(conn, request.app.state.llm, application_id, body.kind, body.question)


def make_draft(conn, llm, application_id: int, kind: str, question: str = "", plan=None, job=None,
               legacy: bool = False) -> DocumentOut:
    """Draft one document and save it as a new version (used by the endpoint and by background preparation)."""
    from app import metrics
    with metrics.collect():                    # joins a preparation's run when there is one
        return _make_draft(conn, llm, application_id, kind, question, plan, job, legacy)


def _make_draft(conn, llm, application_id: int, kind: str, question: str = "", plan=None, job=None,
                legacy: bool = False) -> DocumentOut:
    app = repository.get_application(conn, application_id)
    job = job or repository.get_job(conn, app.job_id)
    profile = repository.get_profile(conn)
    if kind == "resume":
        content, review = tailoring.tailor(llm, profile, job, plan=plan)
        return repository.add_document(conn, application_id, "resume", "", content, "ai", llm.model, review)
    text, draft_review = drafting.draft(llm, profile, job, kind, question, plan=plan, legacy=legacy)
    return repository.add_document(conn, application_id, kind, question, text, "ai", llm.model, draft_review.model_dump())


@router.post("/applications/{application_id}/prepare", response_model=Preparation, status_code=202,
             responses={404: {"description": "Application not found"}})
def start_preparation(application_id: Id, request: Request, conn: Conn):
    """Make the tailored resume and the cover letter in the background. Check progress with GET."""
    repository.get_application(conn, application_id)
    return request.app.state.preparer.start(application_id)


@router.get("/applications/{application_id}/prepare", response_model=Preparation)
def preparation_status(application_id: Id, request: Request):
    """How far the background preparation got: idle, queued, running (with the step), done or failed."""
    return request.app.state.preparer.get(application_id)


@router.get("/applications/{application_id}/documents", response_model=list[DocumentOut])
def list_documents(application_id: Id, conn: Conn):
    """All versions of all materials for this application, newest version first."""
    return repository.list_documents(conn, application_id)


@router.post("/documents/{document_id}/versions", response_model=DocumentOut, status_code=201)
def save_edit(document_id: Id, body: DocumentEdit, conn: Conn):
    """Save your edited text as a new version. The previous version is kept."""
    doc = repository.get_document(conn, document_id)
    if doc.kind == "resume":
        try:
            tailoring.TailoredResume.model_validate_json(body.content)
        except ValidationError as exc:
            raise ConflictError("A resume version must be valid tailored-resume JSON.") from exc
    review = {"method": "user_edit", "edited_from_version": doc.version, "facts_used": [], "warnings": [],
              "questions_for_you": []}
    return repository.add_document(conn, doc.application_id, doc.kind, doc.question, body.content,
                                   "user_edit", None, review)


@router.get("/documents/{document_id}/print", response_class=HTMLResponse,
            responses={404: {"description": "Not found"}, 409: {"description": "Not a resume"}})
def print_resume(document_id: Id, conn: Conn):
    """Printable page for a tailored resume. In the browser: File > Print > Save as PDF."""
    doc = repository.get_document(conn, document_id)
    if doc.kind != "resume":
        raise ConflictError("Only resumes have a printable page.")
    app = repository.get_application(conn, doc.application_id)
    resume = tailoring.TailoredResume.model_validate_json(doc.content)
    return resume_render.render(resume, app.job_title, app.company)


class HandoffItem(BaseModel):
    label: str
    value: str


class Handoff(BaseModel):
    application_id: int
    job_title: str
    company: str
    apply_url: str | None
    status: str
    copy_fields: list[HandoffItem]
    cover_letter: DocumentOut | None
    resume: DocumentOut | None
    screening_answers: list[DocumentOut]
    checklist: list[str]
    warnings: list[str]


@router.get("/applications/{application_id}/handoff", response_model=Handoff,
            responses={404: {"description": "Application or profile not found"}})
def handoff(application_id: Id, conn: Conn):
    """Everything needed to apply by hand on the company's site: links, latest materials, fields to copy.

    The agent never submits for you. After you submit, record the result with PATCH /applications/{id}
    (status submitted_confirmed + your confirmation, or submission_uncertain).
    """
    app = repository.get_application(conn, application_id)
    profile = repository.get_profile(conn)
    docs = repository.list_documents(conn, application_id)   # newest version first per kind/question
    latest = {}
    for d in docs:
        latest.setdefault((d.kind, d.question), d)
    cover = latest.get(("cover_letter", ""))
    resume = latest.get(("resume", ""))
    answers = [d for (kind, _), d in latest.items() if kind == "screening_answer"]

    c, prefs = profile.contact, profile.preferences
    fields = [("Full name", profile.full_name), ("Email", c.email), ("Phone", c.phone),
              ("Location", c.location), ("Address", c.address),
              ("LinkedIn / links", ", ".join(str(link) for link in c.links)),
              ("Work authorization", prefs.work_authorization)]
    warnings = []
    if not resume:
        warnings.append("No tailored resume yet. Click 'Tailor resume' first, or upload your usual resume.")
    for d in [cover, resume, *answers]:
        if d and d.review.get("warnings"):
            label = "cover letter" if d.kind == "cover_letter" else "resume" if d.kind == "resume" else f"answer to '{d.question}'"
            warnings.append(f"The latest {label} (v{d.version}) still has check warnings. Review it before using it.")
    if app.status.value in ("submitted_confirmed", "interviewing", "offer", "rejected"):
        warnings.append(f"This application is already '{app.status.value}'. Don't apply twice.")

    return Handoff(
        application_id=app.id, job_title=app.job_title, company=app.company, apply_url=app.source_url,
        status=app.status.value,
        copy_fields=[HandoffItem(label=k, value=str(v)) for k, v in fields if v],
        cover_letter=cover, resume=resume, screening_answers=answers,
        checklist=[
            "Open the posting and check it is still open.",
            "Open the tailored resume, then File > Print > Save as PDF, and upload that PDF.",
            "Paste the cover letter (or upload it as a PDF) if the form asks for one.",
            "Copy your details and screening answers from this page. Read every answer before pasting.",
            "Submit on the company's site.",
            "Back here: if you got a confirmation (email or on-screen number), mark 'submitted confirmed' and "
            "paste it. If you're not sure it went through, mark 'submission uncertain'.",
        ],
        warnings=warnings,
    )


@router.get("/applications/{application_id}/resume", response_class=HTMLResponse,
            responses={404: {"description": "No tailored resume yet"}})
def latest_resume(application_id: Id, conn: Conn):
    """Printable page of the LATEST tailored resume for this application (stable link: refresh shows new versions)."""
    resumes = [d for d in repository.list_documents(conn, application_id) if d.kind == "resume"]
    if not resumes:
        from app.errors import NotFoundError
        raise NotFoundError("No tailored resume yet for this application. Click 'Tailor resume' first.")
    return print_resume(max(resumes, key=lambda d: d.version).id, conn)
