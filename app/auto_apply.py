"""Approved submissions and their durable outcomes, scoped to a local workspace."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app import career_browser, database, importers, repository, resume_render, submission, tailoring
from app.errors import ConflictError, NotFoundError
from app.schemas import ApplicationStatus, ApplicationUpdate, DocumentOut, ProfileOut

ACTIVE = {"queued", "filling", "submitting"}
BLOCKED_APP_STATUS = submission.ALREADY_SUBMITTED | {"submission_attempted", "submission_uncertain", "withdrawn"}
_STALE_AFTER = timedelta(minutes=12)


class Approval(BaseModel):
    preview: str = Field(pattern=r"^[a-f0-9]{64}$")
    resume_id: int = Field(gt=0)
    cover_letter_id: int | None = Field(None, gt=0)
    answers: dict[str, str] = Field(default_factory=dict, max_length=35)
    screening_answers: dict[str, str] | None = Field(None, max_length=60)
    reviewed_in_browser: bool = False


class Run(BaseModel):
    state: str = "idle"
    message: str = ""
    questions: list[career_browser.Question] = []
    resume_id: int | None = None
    cover_letter_id: int | None = None
    evidence: str | None = None
    uploaded: list[str] = []


class ApprovedPlan(BaseModel):
    target: career_browser.Target
    contact: dict[str, str]
    resume_id: int
    cover_letter_id: int | None = None
    preview: str
    answers: dict[str, str] = {}
    reviewed_in_browser: bool = False
    session_key: str


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(values) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def _contact(profile: ProfileOut, resume: tailoring.TailoredResume | None = None) -> dict[str, str]:
    c = profile.contact
    contact = resume.contact if resume else {}
    name = (resume.name.strip() if resume else "") or profile.full_name
    first, last = submission._split_name(name)
    resume_links = contact.get("links")
    urls = dict.fromkeys(map(str, [*(resume_links if isinstance(resume_links, list) else []), *c.links]))
    links = {}
    for url in urls:
        try:
            parsed = urlsplit(url)
            if parsed.scheme in {"https", "http"} and parsed.hostname and parsed.username is None:
                links[url] = parsed.hostname
        except ValueError:
            continue

    def domain_link(domain):
        return next((u for u, host in links.items() if host == domain or host.endswith("." + domain)), "")

    linkedin, github = domain_link("linkedin.com"), domain_link("github.com")
    website = next((u for u in links if u not in (linkedin, github)), "")
    location = contact.get("location") or c.location or ""
    locality = location.rsplit(",", 1)[-1].strip()
    country = next((name for name in ("Canada", "United States", "United Kingdom", "Australia", "India")
                    if name.casefold() == locality.casefold()), "")
    employers = {role.company for role in profile.experience if role.end_date is None}
    current_company = next(iter(employers)) if len(employers) == 1 else ""
    return {key: value for key, value in {"first_name": first, "last_name": last, "full_name": name,
            "email": contact.get("email") or c.email, "phone": contact.get("phone") or c.phone,
            "location": location, "country": country, "current_company": current_company, "linkedin": linkedin,
            "github": github, "website": website}.items() if value}


def _read(conn, application_id: int):
    return conn.execute("SELECT * FROM auto_apply_runs WHERE application_id=?", (application_id,)).fetchone()


def _status(row) -> Run:
    if row is None:
        return Run()
    data = json.loads(row["data"])
    return Run(state=row["state"], **{k: v for k, v in data.items() if k in Run.model_fields and k != "state"})


def _expired(row) -> bool:
    if _now() - datetime.fromisoformat(row["updated_at"]) > _STALE_AFTER:
        return True
    worker_pid = json.loads(row["data"]).get("worker_pid")
    if os.name == "posix" and worker_pid and worker_pid != os.getpid():
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
    return False


def _write(conn, row, state: str, **details):
    data = {**json.loads(row["data"]), **details}
    conn.execute("UPDATE auto_apply_runs SET state=?, data=?, updated_at=? WHERE application_id=? AND run_token=?",
                 (state, json.dumps(data), _now().isoformat(), row["application_id"], row["run_token"]))


def status(conn, application_id: int) -> Run:
    row = _read(conn, application_id)
    if row and row["state"] in ACTIVE and _expired(row):
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _read(conn, application_id)
            if row and row["state"] in ACTIVE and _expired(row):
                if row["state"] == "submitting":
                    _write(conn, row, "uncertain", message="Submission was interrupted. Check the employer portal before taking further action.")
                    app = repository.get_application(conn, application_id)
                    if app.status is ApplicationStatus.submission_attempted:
                        repository.update_application(conn, application_id, ApplicationUpdate(
                            status=ApplicationStatus.submission_uncertain, event_note="Auto-apply interrupted during submission"))
                else:
                    _write(conn, row, "interrupted", message="The browser worker stopped before submitting. Start the application again when ready.")
                row = _read(conn, application_id)
    return _status(row)


def assert_no_active_run(conn, application_id: int) -> Run:
    run = status(conn, application_id)
    if run.state in ACTIVE:
        raise ConflictError("An automatic submission run is active for this job. Check its status before continuing.")
    return run


def cancel(conn, application_id: int) -> Run:
    repository.get_application(conn, application_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _read(conn, application_id)
        if not row or row["state"] not in {"queued", "filling", "needs_you"}:
            raise ConflictError("There is no pending run that can be stopped before submission.")
        _write(conn, row, "cancelled", message="Auto-apply stopped before submitting.")
        return _status(_read(conn, application_id))


def clear_unreceived(conn, application_id: int) -> Run:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _read(conn, application_id)
        app = repository.get_application(conn, application_id)
        if not row or row["state"] not in {"uncertain", "already_applied"} or app.status.value in submission.ALREADY_SUBMITTED:
            raise ConflictError("There is no uncertain application to clear.")
        message = "You checked the employer portal and confirmed that no application was received."
        _write(conn, row, "cancelled", message=message, questions=[])
        if app.status is not ApplicationStatus.withdrawn:
            repository.update_application(conn, application_id, ApplicationUpdate(
                status=ApplicationStatus.ready_for_review, event_note=message))
        return _status(_read(conn, application_id))


def _portal_url(conn, application_id: int, source_url: str | None) -> str:
    link = conn.execute("SELECT url FROM application_portal_links WHERE application_id=?", (application_id,)).fetchone()
    return link["url"] if link else source_url or ""


def _context(conn, application_id: int, resume_id: int | None = None):
    app = repository.get_application(conn, application_id)
    target = career_browser.target_for(_portal_url(conn, application_id, app.source_url))
    profile = repository.get_profile(conn)
    doc = _doc(conn, application_id, resume_id, "resume") if resume_id else next(
        (d for d in repository.list_documents(conn, application_id) if d.kind == "resume"), None)
    resume = tailoring.TailoredResume.model_validate_json(doc.content) if doc else None
    contact = _contact(profile, resume)
    answers = []
    if target.provider == "greenhouse":
        latest = conn.execute("""SELECT content FROM documents WHERE application_id=? AND kind='application_plan'
                              ORDER BY version DESC LIMIT 1""", (application_id,)).fetchone()
        if latest:
            prior = submission.SubmissionPlan.model_validate_json(latest["content"])
            if prior.approved and career_browser.target_for(prior.apply_url).key == target.key:
                answers = [{"name": f.name, "label": f.label, "value": f.answer} for f in prior.fields
                           if f.answer and not f.sensitive and f.type not in {"file", "hidden"}
                           and not career_browser.contact_key(f.label)]
    base = _digest([app.id, app.job_title, app.company, target.model_dump(), contact, profile.updated_at, answers])
    return app, target, contact, answers, base


def setup(conn, application_id: int, resume_id: int | None = None) -> dict:
    app = repository.get_application(conn, application_id)
    run = status(conn, application_id)
    try:
        _, target, contact, answers, preview = _context(conn, application_id, resume_id or run.resume_id)
    except ConflictError as exc:
        manual_url = career_browser.manual_portal_for(_portal_url(conn, application_id, app.source_url))
        return {"supported": False, "reason": "Complete this application on its posting page." if manual_url else str(exc),
                "manual_url": manual_url, "run": run,
                "can_set_portal": app.status.value not in BLOCKED_APP_STATUS and run.state in {"idle", "cancelled"}}
    docs = repository.list_documents(conn, application_id)
    materials = [{"id": d.id, "version": d.version, "kind": d.kind} for d in docs if d.kind in {"resume", "cover_letter"}]
    blocked = app.status.value in BLOCKED_APP_STATUS or run.state in {"confirmed", "uncertain", "already_applied"}
    return {"supported": True, "provider": target.provider, "url": target.url, "preview": preview,
            "job_title": app.job_title, "company": app.company, "contact": contact, "saved_answers": answers, "materials": materials,
            "blocked": blocked, "run": run, "can_set_portal": not blocked and run.state in {"idle", "cancelled"}}


def save_portal(conn, application_id: int, url: str) -> dict:
    manual_url = career_browser.manual_portal_for(url)
    target = None if manual_url else career_browser.target_for(url)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        app = repository.get_application(conn, application_id)
        row = _read(conn, application_id)
        if app.status.value in BLOCKED_APP_STATUS or (row and row["state"] != "cancelled"):
            raise ConflictError("Choose the employer portal before starting an auto-apply run.")
        if target:
            _duplicate(conn, application_id, target)
        conn.execute("""INSERT INTO application_portal_links (application_id, url) VALUES (?, ?)
                     ON CONFLICT(application_id) DO UPDATE SET url=excluded.url""",
                     (application_id, target.url if target else manual_url))
    return setup(conn, application_id)


_CANADA_STATUS = re.compile(r"(?:canadian (?:permanent resident|citizen)|(?:permanent resident|citizen) of canada|"
                            r"(?:permanent resident|citizen) \(canada\))", re.I)
_CANADA_REGIONS = {"ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt",
                   "alberta", "british columbia", "manitoba", "new brunswick", "newfoundland and labrador",
                   "nova scotia", "northwest territories", "nunavut", "ontario", "prince edward island",
                   "quebec", "saskatchewan", "yukon"}
_CANADA_AUTHORIZATION = re.compile(r"are you (?:currently )?(?:legally )?authori[sz]ed to work in canada")
_CANADA_SPONSORSHIP = re.compile(r"(?:will you|do you) (?:now or in the future )?require sponsorship to "
                                 r"(?:legally )?work in canada")


def _canada_only(location: str) -> bool:
    if any(separator in location for separator in (";", "/", "|", "\n")):
        return False
    parts = [career_browser.label_key(part) for part in location.split(",")]
    if len(parts) == 1:
        return parts[0] in {"canada", "canada remote", "remote canada"}
    if parts.count("canada") != 1 or len(parts) not in {2, 3} or not all(parts):
        return False
    if len(parts) == 2:
        return any(part in _CANADA_REGIONS for part in parts)
    return (parts[-1] == "canada" and parts[1] in _CANADA_REGIONS
            or parts[0] == "canada" and parts[-1] in _CANADA_REGIONS)


def screening_fields(conn, application_id: int, fetch, resume_id: int | None = None) -> dict:
    """Read public Greenhouse question definitions without opening or sending data to the form."""
    _, target, contact, plan, _ = _context(conn, application_id, resume_id)
    if target.provider != "greenhouse":
        return {"questions": [], "cover_letter": None}
    _, board, _, posting = target.path.split("/")
    host = "boards-api.eu.greenhouse.io" if ".eu." in target.host else "boards-api.greenhouse.io"
    try:
        data = fetch(f"https://{host}/v1/boards/{board}/jobs/{posting}?questions=true")
    except (importers.ImportError_, NotFoundError, OSError, ValueError):
        return {"questions": [], "cover_letter": None, "warning": "The employer’s questions could not be loaded in advance."}
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return {"questions": [], "cover_letter": None, "warning": "The employer’s questions could not be loaded in advance."}

    profile = repository.get_profile(conn)
    saved = {career_browser.saved_answer_key(a["name"], a["label"]): a["value"] for a in plan}
    drafts = {}
    for doc in repository.list_documents(conn, application_id):
        if doc.kind == "screening_answer" and not doc.review.get("warnings"):
            drafts.setdefault(career_browser.label_key(doc.question), doc.content)
    prior = _read(conn, application_id)
    prior_data = json.loads(prior["data"]) if prior else {}
    previous = prior_data.get("plan", {})
    browser_draft = prior_data.get("screening_draft", {}) if prior and prior["target_key"] == target.key else {}
    if previous.get("target", {}).get("key") == target.key:
        saved.update(previous.get("answers", {}))
    settled_in_canada = bool(_CANADA_STATUS.fullmatch((profile.preferences.work_authorization or "").strip()))
    verified_canada = _canada_only(str((data.get("location") or {}).get("name") or ""))
    fields = []
    cover_letter = False
    seen = set()
    for question in data["questions"]:
        if not isinstance(question, dict):
            continue
        label = str(question.get("label") or "").strip()[:500]
        if not label:
            continue
        label_id = career_browser.label_key(label)
        required = bool(question.get("required"))
        for field in question.get("fields") or []:
            name, kind = field.get("name", ""), field.get("type", "")
            if name == "cover_letter" and kind == "input_file":
                cover_letter = True
            if not name.startswith("question_") or kind not in submission._TYPE or kind in {"input_hidden", "input_file"}:
                continue
            key = career_browser.saved_answer_key(name, label)
            if key in seen:
                continue
            seen.add(key)
            options = [str(v["label"]) for v in field.get("values", []) if v.get("label")]
            manual = career_browser.requires_browser_review(label)
            draftable = not manual and career_browser.can_draft_screening(label, "textarea" if kind == "textarea" else "text") and not options
            suggestion = "" if manual else saved.get(key, "")
            source = "Previously reviewed" if suggestion else ""
            if not manual and not suggestion and key in browser_draft:
                suggestion, source = browser_draft[key], "Saved from the paused employer form"
            if not manual and not suggestion:
                contact_key = career_browser.contact_key(label)
                if contact_key:
                    suggestion, source = contact.get(contact_key, ""), "Selected résumé and profile"
                elif settled_in_canada and verified_canada and options:
                    work_question = re.sub(r"\s*\(e\.g\., work permit, LMIA-based sponsorship, etc\.\)\??\s*$", "", label, flags=re.I)
                    work_question = career_browser.label_key(work_question)
                    wanted = "No" if _CANADA_SPONSORSHIP.fullmatch(work_question) else "Yes" if _CANADA_AUTHORIZATION.fullmatch(work_question) else ""
                    if wanted in options:
                        suggestion, source = wanted, "Saved Canadian work authorization"
                if not suggestion and draftable and label_id in drafts:
                    suggestion, source = drafts[label_id], "Saved screening draft"
            if options and any(part.strip() not in options for part in suggestion.split(";") if part.strip()):
                suggestion = ""
            fields.append(career_browser.Question(key=key, label=label, options=options, manual=manual,
                                                  required=required, draftable=draftable, suggested=suggestion,
                                                  source=source if suggestion else "",
                                                  multiple=kind == "multi_value_multi_select"))
    return {"questions": fields, "cover_letter": cover_letter}


def review_screening(plan: dict, supplied: dict[str, str]) -> dict[str, str]:
    allowed = {q.key: q for q in plan["questions"] if not q.manual}
    if any(key not in allowed for key in supplied):
        raise ConflictError("The employer’s screening questions changed. Reload them and review your answers.")
    approved = {}
    for key, value in supplied.items():
        question, value = allowed[key], value.strip()
        chosen = [v.strip() for v in value.split(";") if v.strip()] if question.multiple else [value]
        if len(value) > 4000 or (question.options and value and any(v not in question.options for v in chosen)):
            raise ConflictError("Choose one of the employer’s options for: " + question.label)
        if question.multiple and "security clearance" in career_browser.label_key(question.label) and len(chosen) > 1:
            raise ConflictError("Choose your current security clearance status from the employer’s options.")
        approved[key] = "; ".join(chosen) if question.multiple else value
    return approved


def _doc(conn, application_id: int, doc_id: int, kind: str) -> DocumentOut:
    doc = repository.get_document(conn, doc_id)
    if doc.application_id != application_id or doc.kind != kind:
        raise ConflictError(f"Choose a {kind.replace('_', ' ')} from this application.")
    return doc


def _duplicate(conn, application_id: int, target: career_browser.Target):
    if conn.execute("SELECT 1 FROM auto_apply_runs WHERE target_key=? AND application_id<>?", (target.key, application_id)).fetchone():
        raise ConflictError("Another saved application already has a run for this employer posting.")
    placeholders = ",".join("?" for _ in BLOCKED_APP_STATUS)
    rows = conn.execute(f"SELECT COALESCE(p.url, j.source_url) AS source_url FROM applications a JOIN jobs j ON j.id=a.job_id "
                        f"LEFT JOIN application_portal_links p ON p.application_id=a.id "
                        f"WHERE a.id<>? AND a.status IN ({placeholders}) AND COALESCE(p.url, j.source_url) IS NOT NULL",
                        (application_id, *sorted(BLOCKED_APP_STATUS)))
    for row in rows:
        try:
            existing = career_browser.target_for(row["source_url"])
        except ConflictError:
            continue
        if existing.key == target.key:
            raise ConflictError("This employer posting already has a submitted or attempted application.")


def enqueue(conn, application_id: int, approval: Approval, approved_screening: dict | None = None) -> tuple[Run, str | None]:
    status(conn, application_id)  # recover any expired worker before claiming
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        app, target, contact, saved_answers, version = _context(conn, application_id, approval.resume_id)
        row = _read(conn, application_id)
        prior = _status(row)
        if prior.state in ACTIVE:
            return prior, None
        if app.status.value in BLOCKED_APP_STATUS or prior.state in {"confirmed", "uncertain", "already_applied"}:
            raise ConflictError("A submission has already been attempted for this job. Check the employer portal before proceeding.")
        if approval.preview != version:
            raise ConflictError("The posting or your profile changed. Reload the application and review the details again.")
        if approval.cover_letter_id is not None:
            _doc(conn, application_id, approval.cover_letter_id, "cover_letter")
        if approval.reviewed_in_browser and prior.state != "needs_you":
            raise ConflictError("Browser review applies only when the last run paused for you.")
        allowed = {q.key for q in prior.questions if not q.manual}
        if any(key not in allowed or len(value) > 4000 for key, value in approval.answers.items()):
            raise ConflictError("The employer questions changed. Review the pending questions again.")
        _duplicate(conn, application_id, target)
        session = _digest([version, approval.resume_id, approval.cover_letter_id])
        previous_plan = json.loads(row["data"]).get("plan", {}) if row else {}
        answers = {career_browser.saved_answer_key(a["name"], a["label"]): a["value"] for a in saved_answers}
        if previous_plan.get("session_key") == session:
            for key, value in previous_plan.get("answers", {}).items():
                if approved_screening is None or not key.startswith("approved:question_"):
                    answers[key] = value
        answers.update({k: v.strip() for k, v in approval.answers.items() if v.strip()})
        if approved_screening is not None:
            answers.update(approved_screening)
        plan = ApprovedPlan(target=target, contact=contact, preview=version,
                            resume_id=approval.resume_id, cover_letter_id=approval.cover_letter_id,
                            answers=answers, reviewed_in_browser=approval.reviewed_in_browser, session_key=session)
        token = uuid.uuid4().hex
        data = {"message": "Waiting for the application browser.", "worker_pid": os.getpid(), "resume_id": plan.resume_id,
                "cover_letter_id": plan.cover_letter_id, "plan": plan.model_dump(mode="json")}
        conn.execute("""INSERT INTO auto_apply_runs (application_id, target_key, run_token, state, data, updated_at)
                    VALUES (?, ?, ?, 'queued', ?, ?) ON CONFLICT(application_id) DO UPDATE SET target_key=excluded.target_key,
                    run_token=excluded.run_token, state=excluded.state, data=excluded.data, updated_at=excluded.updated_at""",
                     (application_id, target.key, token, json.dumps(data), _now().isoformat()))
        return _status(_read(conn, application_id)), token


def _attachments(conn, application_id: int, plan: ApprovedPlan) -> dict[str, dict]:
    from app.routers.documents import _pdf_bytes, _resume_pdf

    app = repository.get_application(conn, application_id)
    profile = repository.get_profile(conn)
    doc = _doc(conn, application_id, plan.resume_id, "resume")
    resume = tailoring.TailoredResume.model_validate_json(doc.content)
    pdfs = {"resume": (f"Resume-v{doc.version}-{doc.id}.pdf", _resume_pdf(resume, profile, app.job_title, app.company))}
    if plan.cover_letter_id is not None:
        letter = _doc(conn, application_id, plan.cover_letter_id, "cover_letter")
        contact = profile.contact.model_dump(mode="json")
        for key in ("email", "phone", "location"):
            if plan.contact.get(key):
                contact[key] = plan.contact[key]
        html = resume_render.render_letter(letter.content, plan.contact.get("full_name", profile.full_name), contact,
                                           app.job_title, app.company)
        pdfs["cover_letter"] = f"Cover-Letter-v{letter.version}-{letter.id}.pdf", _pdf_bytes(html)
    return {kind: {"name": name, "mimeType": "application/pdf", "buffer": data}
            for kind, (name, data) in pdfs.items()}


def _mark_attempt(path: Path, application_id: int, token: str, plan: ApprovedPlan):
    with closing(database.connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _read(conn, application_id)
        if not row or row["run_token"] != token or row["state"] != "filling":
            raise ConflictError("This browser run is no longer active.")
        app, target, _, _, current = _context(conn, application_id, plan.resume_id)
        if app.status.value in BLOCKED_APP_STATUS or target != plan.target or current != plan.preview:
            raise ConflictError("The job, application status or profile changed before submission.")
        _duplicate(conn, application_id, target)
        _write(conn, row, "submitting", message="Submitting to the employer and checking for confirmation.")
        repository.update_application(conn, application_id, ApplicationUpdate(
            status=ApplicationStatus.submission_attempted, event_note="Auto-apply started final submission"))


def _finish(conn, application_id: int, token: str, result: career_browser.BrowserResult):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _read(conn, application_id)
        if not row or row["run_token"] != token or row["state"] not in ACTIVE:
            return
        old = row["state"]
        state, message = result.state, result.message
        if old == "submitting" and (state != "confirmed" or not result.evidence):
            state, message = "uncertain", "Submit was attempted. Check the employer portal or email before taking further action."
        if old != "submitting" and state in {"confirmed", "uncertain"}:
            state, message = "failed", "The submit attempt could not be verified. Check the employer portal."
        _write(conn, row, state, message=message, evidence=result.evidence if state == "confirmed" else None,
               questions=[q.model_dump() for q in result.questions], uploaded=sorted(result.uploaded))
        if state in {"confirmed", "uncertain"}:
            app = repository.get_application(conn, application_id)
            if app.status is ApplicationStatus.submission_attempted:
                plan = ApprovedPlan.model_validate(json.loads(row["data"])["plan"])
                values = {"submitted_resume_id": plan.resume_id if "resume" in result.uploaded else None,
                          "submitted_cover_letter_id": plan.cover_letter_id if "cover_letter" in result.uploaded else None}
                repository.update_application(conn, application_id, ApplicationUpdate(
                    status=ApplicationStatus.submitted_confirmed if state == "confirmed" else ApplicationStatus.submission_uncertain,
                    submission_confirmation=result.evidence if state == "confirmed" else None,
                    event_note="Auto-apply: " + message, **(values if state == "confirmed" else {})))


class Worker:
    def __init__(self, path: Path, browser_factory):
        self.path = path
        self.jobs: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self.stopping = threading.Event()
        # Separate file and directory even when tests or single-user apps share a parent.
        profile_path = path.parent / (path.stem + "-career-browser")
        self.browser = browser_factory(profile_path)
        self.thread = threading.Thread(target=self._process, daemon=True, name="career-browser")
        self.thread.start()

    def submit(self, application_id, token):
        self.jobs.put((application_id, token))

    def close(self):
        self.stopping.set()
        self.jobs.put(None)

    def _process(self):
        try:
            while (job := self.jobs.get()) is not None:
                if self.stopping.is_set():
                    break
                self._run(*job)
        finally:
            self.browser.close()

    def _run(self, application_id: int, token: str):
        with closing(database.connect(self.path)) as conn:
            try:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = _read(conn, application_id)
                    if not row or row["run_token"] != token or row["state"] != "queued":
                        return
                    _write(conn, row, "filling", message="Opening the employer form and attaching the approved documents.")
                    plan = ApprovedPlan.model_validate(json.loads(row["data"])["plan"])
                    app, target, _, _, current = _context(conn, application_id, plan.resume_id)
                    if app.status.value in BLOCKED_APP_STATUS or target != plan.target or current != plan.preview:
                        raise ConflictError("The job, application status or profile changed. Review the plan again.")
                files = _attachments(conn, application_id, plan)

                def attempt():
                    if self.stopping.is_set():
                        raise ConflictError("The local app is shutting down. This run stopped before submission.")
                    _mark_attempt(self.path, application_id, token, plan)

                result = self.browser.run(application_id, plan.target, plan.contact, plan.answers, files, plan.session_key,
                                          plan.reviewed_in_browser, attempt)
            except ConflictError as exc:
                result = career_browser.BrowserResult("failed", str(exc))
            except Exception:
                # Exceptions from a career portal can contain private form data and credentials.
                result = career_browser.BrowserResult("failed", "The browser could not complete this run. Check the open browser and try again if no submission was attempted.")
            _finish(conn, application_id, token, result)


class Workers:
    def __init__(self, browser_factory=career_browser.CareerBrowser):
        self.factory = browser_factory
        self.sessions: dict[Path, Worker] = {}
        self.lock = threading.Lock()

    def submit(self, path: Path, application_id: int, token: str):
        with self.lock:
            worker = self.sessions.get(path)
            if worker is None:
                worker = self.sessions[path] = Worker(path, self.factory)
            worker.submit(application_id, token)

    def close(self):
        with self.lock:
            for worker in self.sessions.values():
                worker.close()
