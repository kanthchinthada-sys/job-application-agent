"""Faster preparation without losing checks: one shared job analysis, the letter written while the resume is
fitted on the page, model calls one at a time. FICTIONAL data only."""

import json
import threading
import time

from app import preparation, selection
from tests.test_bank import SDET_JOB, with_bank


class SlowModel:
    """Answers like the real model, slowly, and fails the test if two calls ever overlap."""
    model = "slow-fake"

    def __init__(self):
        self.active = 0
        self.order: list[str] = []
        self.overlap = False
        self._lock = threading.Lock()

    def generate_json(self, system, user, schema):
        with self._lock:
            self.active += 1
            self.overlap = self.overlap or self.active > 1
        props = schema.get("properties", {})
        kind = "letter" if {"text", "examples"} & props.keys() else "letter repair" if "fixes" in props else "resume"
        self.order.append(kind)
        time.sleep(0.2)
        with self._lock:
            self.active -= 1
        if kind == "letter repair":
            return {"fixes": [], "additions": []}
        if kind == "letter":
            return {"text": ("I built Java API automation with REST Assured and JUnit 5 for a payments service. " * 12),
                    "fact_ids_used": [], "questions_for_candidate": []}
        return {"bullets": [], "sentences": []}


def _setup(client):
    client.put("/profile", json=json.loads(with_bank().model_dump_json()))
    job_id = client.post("/jobs", json={"title": "Senior SDET", "company": "Example (fictional)",
                                        "description": SDET_JOB.description, "required_skills": ["Java"],
                                        "allow_duplicate": True}).json()["id"]
    return client.post("/applications", json={"job_id": job_id}).json()["id"]


def test_one_analysis_both_documents_and_model_calls_never_overlap(client, db_path, monkeypatch):
    app_id = _setup(client)
    calls = []
    real_plan = selection.plan
    monkeypatch.setattr(selection, "plan", lambda *a, **k: (calls.append(1), real_plan(*a, **k))[1])
    model = SlowModel()
    prep = preparation.Preparer(db_path, model)
    prep.start(app_id)
    prep.wait(timeout=60)
    status = prep.get(app_id)
    assert status.state == "done", status.message
    assert len(calls) == 1                                  # the job is analysed once, for both documents
    assert not model.overlap and model.order[:2] == ["resume", "letter"]   # resume's call first, never two at once
    assert set(model.order[2:]) <= {"letter repair"}                     # at most the letter's repair after it
    t = status.timing
    assert t["model_calls"] >= 2 and "shared: job analysis + evidence selection" in t["stages"]
    docs = {d["kind"]: d for d in client.get(f"/applications/{app_id}/documents").json()}
    assert {"resume", "cover_letter"} <= set(docs)


def test_the_letter_follows_the_resumes_screening_criteria(client, db_path):
    app_id = _setup(client)
    prep = preparation.Preparer(db_path, SlowModel())
    prep.start(app_id)
    prep.wait(timeout=60)
    docs = {d["kind"]: d for d in client.get(f"/applications/{app_id}/documents").json()}
    criteria = [c["requirement"] for c in docs["resume"]["review"]["scorecard"]["criteria"]
                if c["evidence"] in ("direct", "transferable")]
    priorities = [p["requirement"] for p in docs["cover_letter"]["review"]["priorities"]]
    assert priorities and priorities[0] == criteria[0]      # the same first criterion, the same evidence


def test_the_legacy_flow_is_still_available_for_benchmarks(client, db_path):
    app_id = _setup(client)
    model = SlowModel()
    prep = preparation.Preparer(db_path, model, legacy=True)
    prep.start(app_id)
    prep.wait(timeout=60)
    assert prep.get(app_id).state == "done" and model.order[:2] == ["resume", "letter"]


def test_bullets_that_wont_print_are_not_sent_for_rewording(client, fake_llm):
    app_id = _setup(client)
    fake_llm.queue = [{"bullets": []}]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    t = doc["review"]["tailoring"]
    assert "bullets_not_sent_wont_print" in t
    assert doc["review"]["timing"]["model_calls"] == 1      # one call writes all bullets and the summary


def test_the_queue_message_says_waiting_only_when_another_application_is_ahead(db_path):
    from app.preparation import Preparer
    p = Preparer(db_path, llm=None)
    p._pool.submit = lambda *a, **k: None          # nothing runs: we only look at the messages
    assert p.start(1).message == "Starting…"
    assert p.start(2).message.startswith("Waiting for the AI (another application")
