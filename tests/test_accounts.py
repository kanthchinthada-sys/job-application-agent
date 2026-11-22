"""Accounts: sign-in, private workspaces, taking over local data, and the workspace endpoints the screen uses.

Every account name, profile and job here is FICTIONAL."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import database
from app.api import create_app
from tests.conftest import EXAMPLES, FakeLLM


def _app(tmp_path, llm=None):
    return create_app(tmp_path / "job_agent.db", llm=llm or FakeLLM(), accounts=True)


def _sign_up(c, name="alex.fictional", password="correct horse 1"):
    r = c.post("/auth/register", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return r


def _profile():
    return json.loads((EXAMPLES / "fictional_profile.json").read_text())


def _job():
    data = json.loads((EXAMPLES / "fictional_job.json").read_text())
    data["source_url"] = "https://jobs.example.com/fictional/1"
    return data


def test_signed_out_you_see_sign_in_and_create_account_and_the_api_is_closed(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        page = c.get("/ui/", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login"
        login = c.get("/login").text
        assert "Sign in" in login and "Create account" in login
        assert c.get("/jobs").status_code == 401
        assert c.get("/health").status_code == 200


def test_a_new_account_starts_with_an_empty_workspace_and_stays_signed_in_after_a_restart(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = _sign_up(c)
        assert r.json()["new_account"] is True
        assert c.get("/auth/me").json()["username"] == "alex.fictional"
        assert c.get("/jobs").json() == []
        assert c.get("/profile").status_code == 404
        cookie = c.cookies.get("job_agent_session")
    with TestClient(_app(tmp_path)) as c2:                  # the server restarted
        c2.cookies.set("job_agent_session", cookie)
        assert c2.get("/auth/me").json()["username"] == "alex.fictional"


def test_passwords_are_checked_and_never_stored(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        _sign_up(c)
        assert c.post("/auth/register", json={"username": "Alex.Fictional", "password": "x" * 9}).status_code == 400
        assert c.post("/auth/register", json={"username": "sam.fictional", "password": "short"}).status_code == 400
        c.post("/auth/logout")
        assert c.get("/jobs").status_code == 401
        assert c.post("/auth/login", json={"username": "alex.fictional", "password": "wrong one!"}).status_code == 401
        assert c.post("/auth/login", json={"username": "alex.fictional", "password": "correct horse 1"}).status_code == 200
    raw = (tmp_path / "accounts.db").read_bytes()
    assert b"correct horse 1" not in raw


def test_another_account_cannot_see_your_documents_even_by_direct_url(tmp_path):
    llm = FakeLLM()
    llm.answer = {"text": "I'd bring my pytest work (fictional).", "fact_ids_used": ["F1"]}
    app = _app(tmp_path, llm)
    with TestClient(app) as a, TestClient(app) as b:
        _sign_up(a, "alex.fictional")
        a.put("/profile", json=_profile())
        job = a.post("/jobs", json=_job()).json()
        app_id = a.post("/applications", json={"job_id": job["id"]}).json()["id"]
        doc = a.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
        assert a.get(f"/documents/{doc['id']}/download").status_code == 200

        _sign_up(b, "sam.fictional")
        assert b.get("/jobs").json() == [] and b.get("/applications").json() == []
        for url in (f"/documents/{doc['id']}/download", f"/documents/{doc['id']}/print",
                    f"/applications/{app_id}", f"/applications/{app_id}/documents", f"/jobs/{job['id']}"):
            r = b.get(url)
            assert r.status_code == 404 or r.json() == [], (url, r.status_code)
        assert a.get(f"/documents/{doc['id']}/download").status_code == 200     # still yours


def _legacy(tmp_path):
    legacy = tmp_path / "job_agent.db"
    database.init_db(legacy)
    conn = database.connect(legacy)
    conn.execute("INSERT INTO jobs (title, company, description, required_skills, preferred_skills, created_at) "
                 "VALUES ('QA Lead (fictional)', 'Example Corp', 'x', '[\"Python\"]', '[]', '2026-11-01')")
    conn.commit()
    conn.close()
    return legacy


def test_existing_local_data_is_taken_over_only_with_the_owners_setup_code(tmp_path):
    legacy = _legacy(tmp_path)
    app = _app(tmp_path)
    code = app.state.accounts.setup_code()
    assert code and oct((tmp_path / "owner-setup-code.txt").stat().st_mode)[-3:] == "600"   # only you can read it
    with TestClient(app) as first, TestClient(app) as owner:
        _sign_up(first, "first.fictional")                      # registering first grants nothing
        me = first.get("/auth/me").json()
        assert me["local_data_available"] is True and "local_data" not in me      # no counts, no names
        assert first.post("/auth/claim-local-data").status_code == 403
        assert first.post("/auth/claim-local-data", json={"setup_code": "AAAAAA-BBBBBB-CCCCCC"}).status_code == 403
        assert first.get("/jobs").json() == []

        _sign_up(owner, "owner.fictional")
        assert owner.get("/jobs").json() == []                   # nothing moves on its own
        r = owner.post("/auth/claim-local-data", json={"setup_code": code.lower()})
        assert r.status_code == 200 and r.json()["jobs"] == 1
        assert [j["company"] for j in owner.get("/jobs").json()] == ["Example Corp"]
        assert owner.get("/auth/me").json()["local_data_available"] is False
        assert first.post("/auth/claim-local-data", json={"setup_code": code}).status_code == 409   # only once
        assert first.get("/jobs").json() == []
    assert not (tmp_path / "owner-setup-code.txt").exists()                # one use only
    assert sqlite3.connect(legacy).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1   # original kept


def test_wrong_setup_codes_are_rate_limited(tmp_path):
    _legacy(tmp_path)
    with TestClient(_app(tmp_path)) as c:
        _sign_up(c)
        codes = [c.post("/auth/claim-local-data", json={"setup_code": f"WRONG-{i}"}).status_code for i in range(6)]
        assert codes[:5] == [403] * 5 and codes[5] == 429


# ---------- workspace endpoints (single-user mode, as the other tests) ----------

def test_one_failed_document_can_be_retried_alone_and_a_second_start_never_runs_twice(client, fake_llm, profile_data,
                                                                                        make_job):
    from app.llm import LLMOutputError
    client.put("/profile", json=profile_data)
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    import threading
    real = fake_llm.generate_json
    go = threading.Event()             # the model "works" until both clicks are in

    def letter_fails(system, user, schema):
        go.wait(timeout=10)
        from app.drafting import SYSTEM_PROMPT
        if system == SYSTEM_PROMPT:                              # the cover letter's call
            raise LLMOutputError("The model's answer was not valid JSON.")
        return real(system, user, schema)
    fake_llm.generate_json = letter_fails
    fake_llm.queue = [{"bullets": []}, {"sentences": []}]
    first = client.post(f"/applications/{app_id}/prepare").json()
    again = client.post(f"/applications/{app_id}/prepare").json()
    assert again["started_at"] == first["started_at"] and again["state"] in ("queued", "running")   # same run
    go.set()
    client.app.state.preparer.wait()
    p = client.get(f"/applications/{app_id}/prepare").json()
    assert p["state"] == "failed" and p["done_steps"] == ["resume"] and list(p["failed_steps"]) == ["cover_letter"]
    assert "resume is ready" in p["message"]

    fake_llm.generate_json = real
    fake_llm.queue = []
    fake_llm.answer = {"text": "I'd bring my pytest work (fictional).", "fact_ids_used": ["F1"]}
    client.post(f"/applications/{app_id}/prepare", json={"steps": ["cover_letter"]})
    client.app.state.preparer.wait()
    p = client.get(f"/applications/{app_id}/prepare").json()
    assert p["state"] == "done" and p["steps"] == ["cover_letter"], p["message"]
    kinds = [d["kind"] for d in client.get(f"/applications/{app_id}/documents").json()]
    assert kinds.count("resume") == 1 and kinds.count("cover_letter") == 1
    listed = client.get("/applications").json()[0]
    assert set(listed["documents_ready"]) == {"resume", "cover_letter"} and listed["status"] == "preparing"   # application status is separate


def test_opening_the_workspace_makes_no_model_calls(client, fake_llm, profile_data, make_job):
    client.put("/profile", json=profile_data)
    job = make_job()
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    client.patch(f"/applications/{app_id}", json={"notes": "Recruiter: Pat (fictional)."})
    before = len(fake_llm.calls)
    req = client.get(f"/jobs/{job['id']}/requirements").json()
    assert req["rows"] and all({"evidence", "records", "missing"} <= set(r) for r in req["rows"])
    act = client.get(f"/applications/{app_id}/activity").json()
    assert act["notes"] == "Recruiter: Pat (fictional)." and act["items"][0]["type"] == "status"
    client.get(f"/applications/{app_id}/documents")
    client.get(f"/applications/{app_id}/prepare")
    client.get("/applications")
    assert len(fake_llm.calls) == before


def test_pasting_a_saved_posting_again_points_to_the_saved_job_before_any_ai(client, fake_llm):
    text = ("Senior QA Engineer (fictional) at Example Corp. Requirements: Python, pytest, API testing, "
            "CI/CD with Jenkins. Responsibilities: build test automation for payments APIs.")
    first = client.post("/jobs/quick", json={"url": None, "description": text, "use_ai": False})
    assert first.status_code == 201
    calls = len(fake_llm.calls)
    again = client.post("/jobs/quick", json={"url": None, "description": text + "\n", "use_ai": False})
    assert again.status_code == 409 and again.json()["existing_id"] == first.json()["job"]["id"]
    assert len(fake_llm.calls) == calls


def _workspace_with_letter(c, company, llm):
    c.put("/profile", json=_profile())
    job = dict(_job(), company=company, source_url=f"https://jobs.example.com/{company.split()[0].lower()}")
    job_id = c.post("/jobs", json=job).json()["id"]
    app_id = c.post("/applications", json={"job_id": job_id}).json()["id"]
    llm.answer = {"text": f"I'd bring my pytest work to {company}.", "fact_ids_used": ["F1"]}
    doc = c.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    c.patch(f"/applications/{app_id}", json={"notes": f"Notes about {company}"})
    return job_id, app_id, doc["id"]


def test_two_accounts_see_only_their_own_workspace_before_and_after_a_restart(tmp_path):
    llm = FakeLLM()
    app = _app(tmp_path, llm)
    with TestClient(app) as a, TestClient(app) as b:
        _sign_up(a, "alex.fictional")
        _sign_up(b, "sam.fictional")
        assert b.get("/jobs").json() == [] and b.get("/applications").json() == [] and b.get("/profile").status_code == 404
        a_ids = _workspace_with_letter(a, "Alpha Corp (fictional)", llm)
        b_ids = _workspace_with_letter(b, "Beta Corp (fictional)", llm)
        cookies = (a.cookies.get("job_agent_session"), b.cookies.get("job_agent_session"))

    def check(a, b):
        assert [j["company"] for j in a.get("/jobs").json()] == ["Alpha Corp (fictional)"]
        assert [j["company"] for j in b.get("/jobs").json()] == ["Beta Corp (fictional)"]
        # the same numbers exist in both workspaces, but each account only ever gets its own data
        for client, other in ((a, "Beta"), (b, "Alpha")):
            for url in ("/jobs/1", "/applications/1", "/applications/1/documents", "/applications/1/activity",
                        f"/documents/{a_ids[2]}/download", f"/documents/{b_ids[2]}/print", "/profile",
                        "/applications/1/prepare", "/jobs/1/requirements"):
                assert other not in client.get(url).text, (url, other)
        assert b.get("/documents/999/download").status_code == 404

    check(a, b)
    with TestClient(_app(tmp_path, llm)) as a2, TestClient(_app(tmp_path, llm)) as b2:      # server restarted
        a2.cookies.set("job_agent_session", cookies[0])
        b2.cookies.set("job_agent_session", cookies[1])
        check(a2, b2)


def test_a_run_interrupted_by_a_restart_says_so_and_keeps_finished_documents(client, fake_llm, profile_data, make_job,
                                                                              db_path):
    from app.preparation import Preparation
    client.put("/profile", json=profile_data)
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    fake_llm.queue = [{"bullets": [], "sentences": []}]
    client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"})         # finished before the restart
    running = Preparation(application_id=app_id, state="running", step="cover_letter", done_steps=["resume"],
                          steps=["resume", "cover_letter"], started_at="2026-11-21T10:00:00+00:00")
    conn = database.connect(db_path)
    conn.execute("INSERT INTO generation_runs (application_id, data, updated_at) VALUES (?, ?, ?)",
                 (app_id, running.model_dump_json(), "2026-11-21T10:00:00+00:00"))
    conn.commit()
    conn.close()
    with TestClient(create_app(db_path, llm=fake_llm)) as after:                     # the server restarted
        p = after.get(f"/applications/{app_id}/prepare").json()
        assert p["state"] == "interrupted" and list(p["failed_steps"]) == ["cover_letter"]
        assert "Interrupted" in p["failed_steps"]["cover_letter"]
        assert [d["kind"] for d in after.get(f"/applications/{app_id}/documents").json()] == ["resume"]
        fake_llm.answer = {"text": "I'd bring my pytest work (fictional).", "fact_ids_used": ["F1"]}
        after.post(f"/applications/{app_id}/prepare", json={"steps": ["cover_letter"]})
        after.app.state.preparer.wait()
        assert after.get(f"/applications/{app_id}/prepare").json()["state"] == "done"


def test_a_document_interrupted_earlier_keeps_its_retry_while_the_other_is_redone(client, fake_llm, profile_data,
                                                                                   make_job, db_path):
    from app.preparation import Preparation
    client.put("/profile", json=profile_data)
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    running = Preparation(application_id=app_id, state="running", step="resume", steps=["resume", "cover_letter"],
                          started_at="2026-11-21T10:00:00+00:00")
    conn = database.connect(db_path)
    conn.execute("INSERT INTO generation_runs (application_id, data, updated_at) VALUES (?, ?, ?)",
                 (app_id, running.model_dump_json(), "2026-11-21T10:00:00+00:00"))
    conn.commit()
    conn.close()
    with TestClient(create_app(db_path, llm=fake_llm)) as after:
        fake_llm.queue = [{"bullets": [], "sentences": []}]
        after.post(f"/applications/{app_id}/prepare", json={"steps": ["resume"]})
        after.app.state.preparer.wait()
        p = after.get(f"/applications/{app_id}/prepare").json()
        assert p["state"] == "done" and list(p["failed_steps"]) == ["cover_letter"]     # still offered a retry


def test_replacing_the_profile_keeps_documents(client, fake_llm, profile_data, make_job):
    client.put("/profile", json=profile_data)
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    fake_llm.answer = {"text": "Body (fictional).", "fact_ids_used": ["F1"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"})
    client.put("/profile", json=dict(profile_data, full_name="Jordan Sample (fictional)"))
    assert len(client.get(f"/applications/{app_id}/documents").json()) == 1
