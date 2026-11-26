"""Approved submissions and their durable outcomes, scoped to a local workspace."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from app import career_browser, database, repository, resume_render, submission, tailoring
from app.errors import ConflictError
from app.schemas import ApplicationStatus, ApplicationUpdate, DocumentOut, ProfileOut

ACTIVE = {"queued", "filling", "submitting"}
BLOCKED_APP_STATUS = submission.ALREADY_SUBMITTED | {"submission_attempted", "submission_uncertain", "withdrawn"}
_STALE_AFTER = timedelta(minutes=12)


class Approval(BaseModel):
    preview: str = Field(pattern=r"^[a-f0-9]{64}$")
    resume_id: int = Field(gt=0)
    cover_letter_id: int | None = Field(None, gt=0)
    answers: dict[str, str] = Field(default_factory=dict, max_length=35)
    reviewed_in_browser: bool = False


class Run(BaseModel):
    state: str = "idle"
    message: str = ""
    questions: list[career_browser.Question] = []
    resume_id: int | None = None
    cover_letter_id: int | None = None
    evidence: str | None = None


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


def _contact(profile: ProfileOut) -> dict[str, str]:
    c = profile.contact
    first, last = submission._split_name(profile.full_name)
    links = list(map(str, c.links))
    linkedin = next((u for u in links if "linkedin.com/" in u.lower()), "")
    github = next((u for u in links if "github.com/" in u.lower()), "")
    website = next((u for u in links if u not in (linkedin, github)), "")
    locality = (c.location or "").rsplit(",", 1)[-1].strip()
    country = next((name for name in ("Canada", "United States", "United Kingdom", "Australia", "India")
                    if name.casefold() == locality.casefold()), "")
    return {key: value for key, value in {"first_name": first, "last_name": last, "full_name": profile.full_name,
            "email": c.email, "phone": c.phone, "location": c.location, "country": country, "linkedin": linkedin,
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


def _context(conn, application_id: int):
    app = repository.get_application(conn, application_id)
    link = conn.execute("SELECT url FROM application_portal_links WHERE application_id=?", (application_id,)).fetchone()
    target = career_browser.target_for(link["url"] if link else app.source_url or "")
    profile = repository.get_profile(conn)
    contact = _contact(profile)
    answers = []
    if target.provider == "greenhouse":
        latest = conn.execute("""SELECT content FROM documents WHERE application_id=? AND kind='application_plan'
                              ORDER BY version DESC LIMIT 1""", (application_id,)).fetchone()
        if latest:
            prior = submission.SubmissionPlan.model_validate_json(latest["content"])
            if prior.approved and career_browser.target_for(prior.apply_url).key == target.key:
                answers = [{"name": f.name, "label": f.label, "value": f.answer} for f in prior.fields
                           if f.answer and not f.sensitive and f.type not in {"file", "hidden"}
                           and f.name not in {"first_name", "last_name", "email", "phone", "country", "location"}]
    base = _digest([app.id, app.job_title, app.company, target.model_dump(), contact, profile.updated_at, answers])
    return app, target, contact, answers, base


def setup(conn, application_id: int) -> dict:
    app = repository.get_application(conn, application_id)
    run = status(conn, application_id)
    try:
        _, target, contact, answers, preview = _context(conn, application_id)
    except ConflictError as exc:
        return {"supported": False, "reason": str(exc), "run": run,
                "can_set_portal": app.status.value not in BLOCKED_APP_STATUS and run.state in {"idle", "cancelled"}}
    docs = repository.list_documents(conn, application_id)
    materials = [{"id": d.id, "version": d.version, "kind": d.kind} for d in docs if d.kind in {"resume", "cover_letter"}]
    blocked = app.status.value in BLOCKED_APP_STATUS or run.state in {"confirmed", "uncertain", "already_applied"}
    return {"supported": True, "provider": target.provider, "url": target.url, "preview": preview,
            "job_title": app.job_title, "company": app.company, "contact": contact, "saved_answers": answers, "materials": materials,
            "blocked": blocked, "run": run, "can_set_portal": not blocked and run.state in {"idle", "cancelled"}}


def save_portal(conn, application_id: int, url: str) -> dict:
    target = career_browser.target_for(url)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        app = repository.get_application(conn, application_id)
        row = _read(conn, application_id)
        if app.status.value in BLOCKED_APP_STATUS or (row and row["state"] != "cancelled"):
            raise ConflictError("Choose the employer portal before starting an auto-apply run.")
        _duplicate(conn, application_id, target)
        conn.execute("""INSERT INTO application_portal_links (application_id, url) VALUES (?, ?)
                     ON CONFLICT(application_id) DO UPDATE SET url=excluded.url""", (application_id, target.url))
    return setup(conn, application_id)


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


def enqueue(conn, application_id: int, approval: Approval) -> tuple[Run, str | None]:
    status(conn, application_id)  # recover any expired worker before claiming
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        app, target, contact, saved_answers, version = _context(conn, application_id)
        row = _read(conn, application_id)
        prior = _status(row)
        if prior.state in ACTIVE:
            return prior, None
        if app.status.value in BLOCKED_APP_STATUS or prior.state in {"confirmed", "uncertain", "already_applied"}:
            raise ConflictError("A submission has already been attempted for this job. Check the employer portal before proceeding.")
        if approval.preview != version:
            raise ConflictError("The posting or your profile changed. Reload the application and review the details again.")
        _doc(conn, application_id, approval.resume_id, "resume")
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
            answers.update(previous_plan.get("answers", {}))
        answers.update({k: v.strip() for k, v in approval.answers.items() if v.strip()})
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
    from app.routers.documents import _pdf_bytes

    app = repository.get_application(conn, application_id)
    profile = repository.get_profile(conn)
    doc = _doc(conn, application_id, plan.resume_id, "resume")
    resume = tailoring.TailoredResume.model_validate_json(doc.content)
    pdfs = {"resume": (f"Resume-v{doc.version}-{doc.id}.pdf", resume_render.render(resume, app.job_title, app.company))}
    if plan.cover_letter_id is not None:
        letter = _doc(conn, application_id, plan.cover_letter_id, "cover_letter")
        html = resume_render.render_letter(letter.content, profile.full_name, profile.contact.model_dump(mode="json"),
                                           app.job_title, app.company)
        pdfs["cover_letter"] = f"Cover-Letter-v{letter.version}-{letter.id}.pdf", html
    return {kind: {"name": name, "mimeType": "application/pdf", "buffer": _pdf_bytes(html)}
            for kind, (name, html) in pdfs.items()}


def _mark_attempt(path: Path, application_id: int, token: str, plan: ApprovedPlan):
    with closing(database.connect(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _read(conn, application_id)
        if not row or row["run_token"] != token or row["state"] != "filling":
            raise ConflictError("This browser run is no longer active.")
        app, target, _, _, current = _context(conn, application_id)
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
               questions=[q.model_dump() for q in result.questions])
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
                    app, target, _, _, current = _context(conn, application_id)
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
