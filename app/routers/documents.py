import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from app.dependencies import llm as account_llm
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from app import drafting, repository, resume_render, tailoring
from app.errors import ConflictError, NotFoundError
from app.dependencies import get_connection
from typing import Literal

from app.dependencies import preparer
from app.preparation import STEPS, Preparation
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
    return make_draft(conn, account_llm(request), application_id, body.kind, body.question)


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


class PrepareRequest(BaseModel):
    steps: list[Literal["resume", "cover_letter"]] | None = None    # default: both


@router.post("/applications/{application_id}/prepare", response_model=Preparation, status_code=202,
             responses={404: {"description": "Application not found"}})
def start_preparation(application_id: Id, request: Request, conn: Conn, body: PrepareRequest | None = None):
    """Make the tailored resume and/or the cover letter in the background. Check progress with GET.

    Starting again while a run is queued or running returns that run: a refresh or a double click never starts
    a second one. ``steps`` retries one document (e.g. ["cover_letter"] after the letter failed)."""
    repository.get_application(conn, application_id)
    steps = tuple(dict.fromkeys(body.steps)) if body and body.steps else STEPS
    return preparer(request).start(application_id, steps)


@router.get("/applications/{application_id}/prepare", response_model=Preparation)
def preparation_status(application_id: Id, request: Request):
    """How far the background preparation got: idle, queued, running (with the step), done or failed.
    Reading it never calls the model."""
    return preparer(request).get(application_id)


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


_KIND_NAME = {"resume": "Resume", "cover_letter": "Cover letter", "screening_answer": "Screening answer",
              "application_plan": "Application plan"}


def _text_page(text: str, title: str) -> str:
    import html
    paras = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in text.split("\n\n") if p.strip())
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>"
            "body{font-family:Georgia,serif;font-size:11.5pt;line-height:1.45;max-width:7in;margin:0.8in auto;"
            "color:#111}@page{size:letter;margin:0.8in}@media print{body{margin:0}}</style></head>"
            f"<body>{paras}</body></html>")


@router.get("/documents/{document_id}/download", responses={404: {"description": "Not found"}})
def download(document_id: Id, conn: Conn):
    """The document as a plain-text file (a resume as its printed text)."""
    from fastapi.responses import Response
    import re as _re
    doc = repository.get_document(conn, document_id)
    app = repository.get_application(conn, doc.application_id)
    text = doc.content
    if doc.kind == "resume":
        text = tailoring.resume_plain_text(tailoring.TailoredResume.model_validate_json(doc.content))
    name = _re.sub(r"[^A-Za-z0-9]+", "-", f"{_KIND_NAME.get(doc.kind, 'document')}-{app.company}-v{doc.version}")
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name.strip("-").lower()}.txt"'})


@router.get("/documents/{document_id}/print", response_class=HTMLResponse,
            responses={404: {"description": "Not found"}})
def print_resume(document_id: Id, conn: Conn):
    """Printable page for a document (the formatted resume, or a letter). In the browser: Print > Save as PDF."""
    doc = repository.get_document(conn, document_id)
    app = repository.get_application(conn, doc.application_id)
    if doc.kind == "cover_letter":
        try:
            profile = repository.get_profile(conn)
        except NotFoundError:
            pass
        else:
            contact = profile.contact.model_dump(mode="json")
            return HTMLResponse(resume_render.render_letter(doc.content, profile.full_name, contact,
                                                            app.job_title, app.company))
    if doc.kind != "resume":
        return HTMLResponse(_text_page(doc.content, f"{_KIND_NAME.get(doc.kind, 'Document')} · {app.job_title} at "
                                                    f"{app.company}"))
    resume = tailoring.TailoredResume.model_validate_json(doc.content)
    return resume_render.render(resume, app.job_title, app.company)


@router.get("/documents/{document_id}/pdf", responses={404: {"description": "Not found"},
                                                       503: {"description": "No browser to make the PDF"}})
def pdf(document_id: Id, conn: Conn, inline: bool = False):
    """The document as a real PDF file, made here (Chromium prints the same page as Print / Save as PDF), so
    it works in any browser, including ones that cannot open a print dialog. ?inline=1 shows it instead."""
    from fastapi.responses import Response
    import re as _re
    from app import browser as chromium
    from fastapi import HTTPException
    doc = repository.get_document(conn, document_id)
    app = repository.get_application(conn, doc.application_id)
    page = print_resume(document_id, conn)
    html = page.body.decode() if hasattr(page, "body") else str(page)

    def make(b):
        pg = chromium.print_page(b, html)
        try:
            pg.evaluate("() => window.smartJustify && window.smartJustify()")
            return pg.pdf(format="Letter", prefer_css_page_size=True, print_background=True)
        finally:
            pg.close()
    try:
        data = chromium.run(make, timeout=60)
    except Exception as exc:
        raise HTTPException(503, "Could not make the PDF here (no browser available). Use Print / Save as PDF "
                                 f"instead. ({type(exc).__name__})") from exc
    name = _re.sub(r"[^A-Za-z0-9]+", "-", f"{_KIND_NAME.get(doc.kind, 'document')}-{app.company}-v{doc.version}")
    how = "inline" if inline else "attachment"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'{how}; filename="{name.strip("-").lower()}.pdf"'})


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
