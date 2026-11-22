"""The model's part of adding a job, after the job is saved ("intake_runs_v1").

Adding a job never waits for the model: the job is saved at once with what the job board, the page's structured
data and the rules found (app/job_intake.py), and you can check and correct it straight away. Only when something
is missing or uncertain (no title, no reliable company, fewer than three skills) is the model asked, here, in the
background, with a timeout and bounded retries.

Its answer fills a field only if
* the field was one of the missing ones,
* the posting backs the value (job_intake.apply_model_answer), and
* you haven't changed that field since the job was saved (a correction you made always wins, even if the model
  answers later).

Progress is kept in the workspace's own database (table intake_runs), so GET /jobs/{id}/intake answers after a
restart too: a run that was cut short by a restart shows as failed, with the rules' fields kept.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app import database, job_intake
from app.job_intake import Intake

FIELDS = ("title", "company", "location", "skills")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _snapshot(row) -> dict:
    return {"title": row["title"], "company": row["company"], "location": row["location"],
            "skills": [json.loads(row["required_skills"]), json.loads(row["preferred_skills"])]}


class IntakeWorker:
    def __init__(self, db_path: Path, llm):
        self.db_path, self.llm = db_path, llm
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intake")
        self._done: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._mark_interrupted()

    # --- saved state -----------------------------------------------------------------------------------------
    def _load(self, conn, job_id: int) -> dict | None:
        row = conn.execute("SELECT data FROM intake_runs WHERE job_id = ?", (job_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def _store(self, conn, job_id: int, data: dict) -> None:
        conn.execute("INSERT INTO intake_runs (job_id, data, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(job_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                     (job_id, json.dumps(data), _now()))

    def _mark_interrupted(self) -> None:
        try:
            conn = database.connect(self.db_path)
        except Exception:
            return
        try:
            for row in conn.execute("SELECT job_id, data FROM intake_runs").fetchall():
                data = json.loads(row["data"])
                if data.get("ai_state") == "pending":
                    data["ai_state"] = "failed"
                    data["notes"] = data.get("notes", []) + [
                        "The app restarted before the AI finished: the fields read from the posting are kept."]
                    data["finished_at"] = _now()
                    self._store(conn, row["job_id"], data)
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    # --- API -------------------------------------------------------------------------------------------------
    def start(self, conn, job_id: int, info: Intake, description: str, url: str | None) -> None:
        """Called right after the job is saved (same connection): record what was saved, then ask the model."""
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        data = {"ai_state": "pending", "missing": info.missing, "review": info.review, "notes": info.notes,
                "found_by": info.found_by, "saved": _snapshot(row), "user_fields": [], "applied": [], "kept": [],
                "attempts": [], "started_at": _now(), "finished_at": None}
        self._store(conn, job_id, data)
        conn.commit()
        with self._lock:
            self._done[job_id] = threading.Event()
        self._pool.submit(self._run, job_id, info.model_copy(deep=True), description, url)

    def get(self, conn, job_id: int) -> dict | None:
        return self._load(conn, job_id)

    def lock(self, conn, job_id: int, fields: set[str]) -> None:
        """You changed these fields: the model may never fill them. Called BEFORE your change is saved."""
        data = self._load(conn, job_id)
        if data is None:
            return
        data["user_fields"] = sorted(set(data["user_fields"]) | (fields & set(FIELDS)))
        data["review"] = [f for f in data["review"] if f not in fields]
        self._store(conn, job_id, data)
        conn.commit()

    def wait(self, job_id: int, timeout: float = 10.0) -> bool:
        """Tests and benchmarks: block until the job's background step has finished."""
        with self._lock:
            event = self._done.get(job_id)
        return event.wait(timeout) if event else True

    # --- the background step ---------------------------------------------------------------------------------
    def _run(self, job_id: int, info: Intake, description: str, url: str | None) -> None:
        attempts: list[dict] = []
        answer, error = None, None
        try:
            answer = job_intake.ask_model(description, url, self.llm, attempts)
        except Exception as exc:                 # the model is optional: the rules' fields stay
            error = exc
        conn = database.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")      # nobody else writes this job while we compare and apply
            data = self._load(conn, job_id)
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if data is None or row is None:      # the job was deleted meanwhile
                conn.rollback()
                return
            data["attempts"] = attempts
            data["finished_at"] = _now()
            if error is not None:
                data["ai_state"] = "failed"
                data["notes"] = data["notes"] + [f"AI not used ({job_intake._why(error)}): the fields read from the "
                                                 "posting are kept. Check the highlighted ones."]
            else:
                changed = job_intake.apply_model_answer(info, answer, description, url)
                now = _snapshot(row)
                sets: dict[str, object] = {}
                for field in changed:
                    if field in data["user_fields"] or now[field] != data["saved"][field]:
                        data["kept"].append(field)          # you corrected it: yours stays
                        continue
                    if field == "skills":
                        sets["required_skills"] = json.dumps(info.required_skills)
                        sets["preferred_skills"] = json.dumps(info.preferred_skills)
                    else:
                        sets[field] = getattr(info, field)
                    data["applied"].append(field)
                    data["found_by"][field] = info.found_by.get(field, "AI (checked against the posting)")
                if sets:
                    conn.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                                 (*sets.values(), job_id))
                # the model read the same value as the rules, and you haven't changed it: no need to check it
                confirmed = [f for f in info.confirmed if f not in data["user_fields"] and now[f] == data["saved"][f]]
                data["confirmed"] = confirmed
                for f in confirmed:
                    data["found_by"][f] = info.found_by.get(f, data["found_by"].get(f, "rules"))
                data["review"] = [f for f in data["review"] if f not in data["applied"] and f not in confirmed]
                data["notes"] = [n for n in data["notes"]
                                 if not (("company" in data["applied"] + confirmed and n.startswith(("No company", "Company ")))
                                         or ("title" in data["applied"] + confirmed and n.startswith("No job title")))]
                data["ai_state"] = "done"
            self._store(conn, job_id, data)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            with self._lock:
                event = self._done.get(job_id)
            if event:
                event.set()
