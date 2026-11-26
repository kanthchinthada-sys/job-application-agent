"""Prepare an application's materials in the background ("Apply" on Find jobs).

The tailored resume and the cover letter take a minute or two with a local AI model. They are made here, on
the server, one application at a time, so the work continues whatever screen you open (or if you close the
tab). Any screen can ask how far it got: GET /applications/{id}/prepare.

Progress is kept in memory: after a restart, finished documents are of course still saved; an interrupted
preparation simply shows as not started, and you can start it again.
"""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from app import database
from app.errors import NotFoundError
from app.llm import LLMOutputError, LLMUnavailableError

STEPS = ("resume", "cover_letter")
LABEL = {"resume": "Tailoring your resume", "cover_letter": "Writing your cover letter"}


class _OneAtATime:
    """The model client, one call at a time (a local model answers one request well, not two at once).

    Waiting for the model is not counted against a call's timeout, and `started` tells the cover letter when
    the resume's call has begun, so the letter's call queues right behind it instead of in front of it."""

    def __init__(self, llm, parallel: int = 1):
        from app.llm import writes_whole_resume
        self._llm, self.model = llm, getattr(llm, "model", "")
        # Claude or OpenAI: the resume and the letter are written at the same time; the local model, one at a time
        self.writes_whole_resume = writes_whole_resume(llm)
        self._slots = threading.Semaphore(max(2 if self.writes_whole_resume else 1, parallel))
        self.started = threading.Event()

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        import time

        from app import metrics
        t = time.perf_counter()
        with self._slots:
            metrics.note_usage(queue_seconds=round(time.perf_counter() - t, 3))
            self.started.set()
            return self._llm.generate_json(system, user, schema)

    def status(self) -> dict:
        return self._llm.status()


class Preparation(BaseModel):
    application_id: int
    state: str                       # idle | queued | running | done | failed | interrupted (server restarted)
    step: str | None = None          # resume | cover_letter (while running)
    message: str = ""
    done_steps: list[str] = []
    failed_steps: dict[str, str] = {}   # step -> why it failed (the other document may still be fine)
    steps: list[str] = list(STEPS)      # what this run makes
    started_at: str | None = None
    finished_at: str | None = None
    timing: dict | None = None       # stages, model calls, tokens (app/metrics.py)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Preparer:
    def __init__(self, db_path: Path, llm, legacy: bool = False):
        """legacy=True: the old flow (resume, then letter, each analysing the job); for benchmarks."""
        import os
        self.db_path, self.llm, self.legacy = db_path, llm, legacy
        self.parallel = int(os.environ.get("LLM_PARALLEL", "1") or 1)   # >1 only if your model server allows it
        self._status: dict[int, Preparation] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prepare")   # one at a time: one local AI
        self._mark_interrupted()
        try:
            with closing(database.connect(self.db_path)) as conn:
                requests = conn.execute("SELECT application_id FROM resume_refresh_requests").fetchall()
        except sqlite3.OperationalError as exc:
            # The normal app initializes the workspace first. Standalone queue callers may not.
            if "no such table" not in str(exc):
                raise
            requests = []
        for row in requests:
            self.refresh_resume(row["application_id"])

    # --- saved progress (this workspace's database): a restart never loses track of a run -----------------
    def _save(self, p: Preparation, *, consume_refresh: bool = False) -> None:
        try:
            conn = database.connect(self.db_path)
            try:
                conn.execute("INSERT OR REPLACE INTO generation_runs (application_id, data, updated_at) VALUES (?, ?, ?)",
                             (p.application_id, p.model_dump_json(), _now()))
                if consume_refresh:
                    conn.execute("DELETE FROM resume_refresh_requests WHERE application_id=?", (p.application_id,))
                conn.commit()
            finally:
                conn.close()
        except Exception:                    # progress is a convenience: never fail a run because of it
            pass

    def _saved(self, application_id: int) -> Preparation | None:
        try:
            conn = database.connect(self.db_path)
            try:
                row = conn.execute("SELECT data FROM generation_runs WHERE application_id = ?", (application_id,)).fetchone()
            finally:
                conn.close()
            return Preparation.model_validate_json(row["data"]) if row else None
        except Exception:
            return None

    def _mark_interrupted(self) -> None:
        """Runs left queued or running by a previous server process were interrupted: say so, per document."""
        try:
            conn = database.connect(self.db_path)
            try:
                rows = conn.execute("SELECT data FROM generation_runs").fetchall()
            finally:
                conn.close()
        except Exception:
            return
        for row in rows:
            p = Preparation.model_validate_json(row["data"])
            if p.state in ("queued", "running"):
                left = [s_ for s_ in p.steps if s_ not in p.done_steps and s_ not in p.failed_steps]
                why = "Interrupted: the app stopped before this was finished. Your other documents are safe."
                self._save(p.model_copy(update={
                    "state": "interrupted", "step": None, "finished_at": _now(),
                    "failed_steps": {**p.failed_steps, **{s_: why for s_ in left}},
                    "message": "Generation was interrupted (the app was restarted). Retry the unfinished "
                               + ("document." if len(left) == 1 else "documents.")}))

    def get(self, application_id: int) -> Preparation:
        with self._lock:
            p = self._status.get(application_id)
            if p:
                return p.model_copy()
        return self._saved(application_id) or Preparation(application_id=application_id, state="idle")

    def start(self, application_id: int, steps: tuple[str, ...] = STEPS, *,
              consume_refresh: bool = False) -> Preparation:
        with self._lock:
            current = self._status.get(application_id)
            if current and current.state in ("queued", "running"):
                return current.model_copy()                      # already on its way: never twice
            # only say "waiting" when another application really is ahead of this one
            busy = any(p.state in ("queued", "running") for aid, p in self._status.items() if aid != application_id)
            # a document that failed or was interrupted before, and isn't part of this run, keeps its retry
            before = current or self._saved(application_id)
            carried = {k: v for k, v in (before.failed_steps if before else {}).items() if k not in steps}
            self._status[application_id] = current = Preparation(
                application_id=application_id, state="queued", started_at=_now(), steps=list(steps),
                failed_steps=carried,
                message="Waiting for the AI (another application is being prepared)…" if busy else "Starting…")
        self._save(current, consume_refresh=consume_refresh)
        self._pool.submit(self._run, application_id, steps)
        return self.get(application_id)

    def refresh_resume(self, application_id: int) -> Preparation:
        """Coalesce confirmations that arrive while this application's documents are generating."""
        with self._lock:
            current = self.get(application_id)
            if current.state in ("queued", "running"):
                return current
            with closing(database.connect(self.db_path)) as conn:
                pending = conn.execute("SELECT 1 FROM resume_refresh_requests WHERE application_id=?",
                                       (application_id,)).fetchone()
            if pending:
                return self.start(application_id, ("resume",), consume_refresh=True)
            return current

    def _set(self, application_id: int, **changes) -> None:
        with self._lock:
            p = self._status[application_id].model_copy(update=changes)
            self._status[application_id] = p
        if set(changes) - {"timing"}:          # state changes are saved; timing is shown live only
            self._save(p)

    def _run(self, application_id: int, steps: tuple[str, ...]) -> None:
        from app import metrics
        with metrics.collect(new=True) as m:
            try:
                self._run_steps(application_id, steps)
            finally:
                self._set(application_id, timing=m.report())
                self.refresh_resume(application_id)

    def _run_steps(self, application_id: int, steps: tuple[str, ...]) -> None:
        import contextvars

        from app import bank, metrics, repository
        from app import selection as sel
        from app.routers.documents import make_draft
        from app.tailoring import _usable_skills
        conn = database.connect(self.db_path)
        done: list[str] = []
        failed: dict[str, str] = dict(self.get(application_id).failed_steps)    # carried from earlier runs

        def attempt(step: str, run) -> None:
            """One document: its failure is recorded and never stops the other one."""
            try:
                run()
                done.append(step)
            except NotFoundError as exc:
                failed[step] = ("Add your profile first (Profile: upload your resume), then generate again."
                                if "profile" in str(exc).lower() else str(exc))
            except LLMUnavailableError as exc:
                failed[step] = f"The local AI isn't available: {exc}"
            except Exception as exc:                       # LLMOutputError and anything else: never stuck
                failed[step] = f"Could not finish ({exc.__class__.__name__}): {str(exc)[:200]}"
            self._set(application_id, done_steps=list(done), failed_steps=dict(failed))

        try:
            if self.legacy or set(steps) != set(STEPS):
                for step in steps:              # one document, or the old flow: one after the other
                    self._set(application_id, state="running", step=step, message=f"{LABEL[step]}…")
                    attempt(step, lambda step=step: make_draft(conn, self.llm, application_id, step, legacy=self.legacy))
            else:
                # 1. analyse the job and match it to your experience ONCE, for both documents
                self._set(application_id, state="running", step="resume", message=f"{LABEL['resume']}…")
                with metrics.stage("shared: job analysis + evidence selection"):
                    app = repository.get_application(conn, application_id)
                    job = _usable_skills(repository.get_job(conn, app.job_id))
                    profile = repository.get_profile(conn)
                    plan = sel.plan(profile, job) if bank.verified(profile) else None
                llm = _OneAtATime(self.llm, self.parallel)
                ctx = contextvars.copy_context()

                def letter():
                    # starts once the resume's model call has begun, so it queues right behind it and runs
                    # while the resume is checked and fitted on the page
                    llm.started.wait(timeout=600)
                    c = database.connect(self.db_path)
                    try:
                        attempt("cover_letter", lambda: make_draft(c, llm, application_id, "cover_letter",
                                                                   plan=plan, job=job))
                    finally:
                        c.close()

                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="letter") as pool:
                    letter_done = pool.submit(ctx.run, letter)
                    try:
                        attempt("resume", lambda: make_draft(conn, llm, application_id, "resume", plan=plan, job=job))
                    finally:
                        llm.started.set()                  # never leave the letter waiting
                    self._set(application_id, step="cover_letter", message=f"{LABEL['cover_letter']}…")
                    letter_done.result()
            if any(k in steps for k in failed):
                names = {"resume": "resume", "cover_letter": "cover letter"}
                ok = " and ".join(names[d] for d in done)
                self._set(application_id, state="failed", step=None, finished_at=_now(),
                          message=(f"The {ok} {'is' if len(done) == 1 else 'are'} ready; " if done else "")
                          + "; ".join(f"the {names[k]} failed: {v}" for k, v in failed.items() if k in steps))
            else:
                ready = {("resume",): "The resume is ready.", ("cover_letter",): "The cover letter is ready."}
                self._set(application_id, state="done", step=None, finished_at=_now(),
                          message=ready.get(tuple(steps), "Resume and cover letter are ready."))
        except NotFoundError as exc:
            msg = ("Add your profile first (Profile: upload your resume), then generate again."
                   if "profile" in str(exc).lower() else str(exc))
            self._set(application_id, state="failed", step=None, finished_at=_now(), message=msg,
                      failed_steps={s_: msg for s_ in steps if s_ not in done})
        except LLMUnavailableError as exc:
            msg = f"The local AI isn't available: {exc}"
            self._set(application_id, state="failed", step=None, finished_at=_now(), message=msg,
                      failed_steps={s_: msg for s_ in steps if s_ not in done})
        except (LLMOutputError, Exception) as exc:                 # never leave it "running" forever
            msg = f"Could not finish ({exc.__class__.__name__}): {str(exc)[:200]}"
            self._set(application_id, state="failed", step=None, finished_at=_now(), message=msg,
                      failed_steps={s_: msg for s_ in steps if s_ not in done})
        finally:
            conn.close()

    def wait(self, application_id: int | None = None, timeout: float = 30) -> None:
        """Tests and benchmarks: wait until queued work is finished."""
        self._pool.submit(lambda: None).result(timeout=timeout)
