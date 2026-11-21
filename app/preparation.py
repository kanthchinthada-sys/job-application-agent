"""Prepare an application's materials in the background ("Apply" on Find jobs).

The tailored resume and the cover letter take a minute or two with a local AI model. They are made here, on
the server, one application at a time, so the work continues whatever screen you open (or if you close the
tab). Any screen can ask how far it got: GET /applications/{id}/prepare.

Progress is kept in memory: after a restart, finished documents are of course still saved; an interrupted
preparation simply shows as not started, and you can start it again.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
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
        self._llm, self.model = llm, getattr(llm, "model", "")
        self._slots = threading.Semaphore(max(1, parallel))
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
    state: str                       # idle | queued | running | done | failed
    step: str | None = None          # resume | cover_letter (while running)
    message: str = ""
    done_steps: list[str] = []
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
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prepare")   # one at a time: one local AI

    def get(self, application_id: int) -> Preparation:
        with self._lock:
            p = self._status.get(application_id)
            return p.model_copy() if p else Preparation(application_id=application_id, state="idle")

    def start(self, application_id: int, steps: tuple[str, ...] = STEPS) -> Preparation:
        with self._lock:
            current = self._status.get(application_id)
            if current and current.state in ("queued", "running"):
                return current.model_copy()                      # already on its way: never twice
            # only say "waiting" when another application really is ahead of this one
            busy = any(p.state in ("queued", "running") for aid, p in self._status.items() if aid != application_id)
            self._status[application_id] = Preparation(
                application_id=application_id, state="queued", started_at=_now(),
                message="Waiting for the AI (another application is being prepared)…" if busy else "Starting…")
        self._pool.submit(self._run, application_id, steps)
        return self.get(application_id)

    def _set(self, application_id: int, **changes) -> None:
        with self._lock:
            p = self._status[application_id]
            self._status[application_id] = p.model_copy(update=changes)

    def _run(self, application_id: int, steps: tuple[str, ...]) -> None:
        from app import metrics
        with metrics.collect(new=True) as m:
            try:
                self._run_steps(application_id, steps)
            finally:
                self._set(application_id, timing=m.report())

    def _run_steps(self, application_id: int, steps: tuple[str, ...]) -> None:
        import contextvars

        from app import bank, metrics, repository
        from app import selection as sel
        from app.routers.documents import make_draft
        from app.tailoring import _usable_skills
        conn = database.connect(self.db_path)
        done: list[str] = []
        try:
            if self.legacy or set(steps) != set(STEPS):
                for step in steps:              # the old flow: one document after the other
                    self._set(application_id, state="running", step=step, message=f"{LABEL[step]}…")
                    make_draft(conn, self.llm, application_id, step, legacy=self.legacy)
                    done.append(step)
                    self._set(application_id, done_steps=list(done))
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
                        return make_draft(c, llm, application_id, "cover_letter", plan=plan, job=job)
                    finally:
                        c.close()

                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="letter") as pool:
                    letter_done = pool.submit(ctx.run, letter)
                    try:
                        make_draft(conn, llm, application_id, "resume", plan=plan, job=job)
                    finally:
                        llm.started.set()                  # never leave the letter waiting
                    done.append("resume")
                    self._set(application_id, step="cover_letter", done_steps=list(done),
                              message=f"{LABEL['cover_letter']}…")
                    letter_done.result()
                    done.append("cover_letter")
                    self._set(application_id, done_steps=list(done))
            self._set(application_id, state="done", step=None, finished_at=_now(),
                      message="Resume and cover letter are ready.")
        except NotFoundError as exc:
            msg = ("Add your profile first (Profile tab: upload your resume), then press Prepare again."
                   if "profile" in str(exc).lower() else str(exc))
            self._set(application_id, state="failed", step=None, finished_at=_now(), message=msg)
        except LLMUnavailableError as exc:
            self._set(application_id, state="failed", step=None, finished_at=_now(),
                      message=f"The local AI isn't available: {exc}")
        except (LLMOutputError, Exception) as exc:                 # never leave it "running" forever
            self._set(application_id, state="failed", step=None, finished_at=_now(),
                      message=f"Could not finish ({exc.__class__.__name__}): {str(exc)[:200]}")
        finally:
            conn.close()

    def wait(self, application_id: int | None = None, timeout: float = 30) -> None:
        """Tests and benchmarks: wait until queued work is finished."""
        self._pool.submit(lambda: None).result(timeout=timeout)
