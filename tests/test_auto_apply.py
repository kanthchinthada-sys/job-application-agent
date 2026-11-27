import json
import threading
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import auto_apply, career_browser, database, repository
from app.api import create_app
from app.errors import ConflictError


@pytest.fixture
def auto_client(db_path, fake_llm, profile_data, monkeypatch):
    class Browser:
        def __init__(self, profile_dir):
            self.calls = []
            self.mode = "confirm"
            self.entered = threading.Event()
            self.release = threading.Event()
            self.profile_dir = profile_dir

        def run(self, app_id, target, contact, answers, files, key, reviewed, mark_attempt):
            self.calls.append((app_id, target, contact, answers, files, key, reviewed))
            self.entered.set()
            self.release.wait(5)
            if self.mode == "pause":
                return career_browser.BrowserResult("needs_you", "Needs an answer", [
                    career_browser.Question(key="source-question", label="How did you hear about the job?"),
                    career_browser.Question(key="consent", label="Privacy and consent", manual=True)])
            mark_attempt()
            if self.mode == "timeout":
                raise RuntimeError("browser interrupted after click")
            return career_browser.BrowserResult("confirmed", "The employer confirmed the submission.",
                                               evidence="Fictional confirmation", uploaded=set(files))

        def close(self):
            pass

    browser = Browser(db_path.parent / "fake-browser")
    monkeypatch.setattr(auto_apply, "_attachments", lambda conn, app, plan: {
        "resume": {"name": "fixture.pdf", "buffer": b"fixture", "id": plan.resume_id},
        **({"cover_letter": {"name": "fixture-letter.pdf", "id": plan.cover_letter_id}} if plan.cover_letter_id else {}),
    })
    with TestClient(create_app(db_path, llm=fake_llm, career_browser_factory=lambda directory: browser)) as client:
        client.put("/profile", json=profile_data).raise_for_status()
        try:
            yield client, browser
        finally:
            browser.release.set()


def _application(client, job_data, source="https://boards.greenhouse.io/fictional/jobs/111"):
    job = client.post("/jobs", json={**job_data, "source_url": source, "allow_duplicate": True}).raise_for_status().json()
    app = client.post("/applications", json={"job_id": job["id"]}).raise_for_status().json()["id"]
    doc = client.post(f"/applications/{app}/drafts", json={"kind": "resume"}).raise_for_status().json()
    return app, doc


def _setup(client, app):
    route = f"/applications/{app}/auto-apply"
    return route, client.get(route).raise_for_status().json()


def _wait(client, route, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = client.get(route).raise_for_status().json()["run"]
        if run["state"] == expected:
            return run
        time.sleep(.03)
    pytest.fail(f"Expected {expected}, got {run['state']}: {run['message']}")


def test_one_approved_run_saves_exact_versions_and_blocks_alternative_urls(auto_client, job_data, db_path):
    client, browser = auto_client
    app_id, original = _application(client, job_data)
    client.post(f"/documents/{original['id']}/versions", json={"content": original["content"]}).raise_for_status()
    with closing(database.connect(db_path)) as conn:
        letter = repository.add_document(conn, app_id, "cover_letter", "", "A fictional cover letter.", "user_edit", None, {})
    route, setup = _setup(client, app_id)
    assert setup["provider"] == "greenhouse" and setup["run"]["state"] == "idle"
    payload = {"preview": setup["preview"], "resume_id": original["id"], "cover_letter_id": letter.id}
    assert client.post(route, json={**payload, "preview": "0" * 64}).status_code == 409
    assert client.post(route, json={**payload, "resume_id": letter.id}).status_code == 409
    assert client.post(route, json=payload).raise_for_status().json()["state"] == "queued"
    assert browser.entered.wait(4)
    client.post(route, json=payload).raise_for_status()
    assert client.get(f"/applications/{app_id}/submission/ready").status_code == 409
    browser.release.set()
    _wait(client, route, "confirmed")
    app = client.get(f"/applications/{app_id}").json()
    assert app["status"] == "submitted_confirmed" and app["submitted_resume_id"] == original["id"]
    assert app["submitted_cover_letter_id"] == letter.id
    assert app["submission_confirmation"] == "Fictional confirmation"
    assert len(browser.calls) == 1
    assert client.post(route, json=payload).status_code == 409
    duplicate, duplicate_doc = _application(client, job_data, "https://job-boards.greenhouse.io/fictional/jobs/111?source=second")
    second_route, second = _setup(client, duplicate)
    assert client.post(second_route, json={"preview": second["preview"], "resume_id": duplicate_doc["id"]}).status_code == 409


def test_resume_with_reviewed_answers_and_stop_after_uncertain_result(auto_client, job_data):
    client, browser = auto_client
    app_id, doc = _application(client, job_data, "https://jobs.lever.co/fictional/123")
    route, setup = _setup(client, app_id)
    payload = {"preview": setup["preview"], "resume_id": doc["id"]}
    browser.mode = "pause"
    browser.release.set()
    client.post(route, json=payload).raise_for_status()
    _wait(client, route, "needs_you")
    assert client.post(route, json={**payload, "answers": {"consent": "I agree"}}).status_code == 409
    browser.mode = "timeout"
    retry = {**payload, "reviewed_in_browser": True, "answers": {"source-question": "Company website"}}
    client.post(route, json=retry).raise_for_status()
    _wait(client, route, "uncertain")
    assert browser.calls[-1][3] == {"source-question": "Company website"}
    assert browser.calls[-1][-1] is True
    assert client.get(f"/applications/{app_id}").json()["status"] == "submission_uncertain"
    assert client.post(route, json=retry).status_code == 409


def test_cancel_prevents_final_click(auto_client, job_data):
    client, browser = auto_client
    app, doc = _application(client, job_data, "https://jobs.ashbyhq.com/fictional/abc")
    route, setup = _setup(client, app)
    client.post(route, json={"preview": setup["preview"], "resume_id": doc["id"]}).raise_for_status()
    assert browser.entered.wait(4)
    assert client.post(route + "/cancel").raise_for_status().json()["state"] == "cancelled"
    browser.release.set()
    assert client.get(f"/applications/{app}").json()["status"] == "preparing"


def test_browser_routes_are_local_only(client, job_data, profile_data):
    client.put("/profile", json=profile_data).raise_for_status()
    app, doc = _application(client, job_data)
    route, setup = _setup(client, app)
    assert not setup["supported"]
    assert client.post(route, json={"preview": "0" * 64, "resume_id": doc["id"]}).status_code == 403


def test_existing_aggregator_application_can_use_a_direct_employer_link(auto_client, job_data):
    client, _ = auto_client
    original = "https://jobs.example.com/fictional/42"
    app, _ = _application(client, job_data, original)
    route, setup = _setup(client, app)
    assert not setup["supported"] and setup["can_set_portal"]
    assert client.put(route + "/portal", json={"url": original}).status_code == 409
    manual = "https://ca.linkedin.com/jobs/view/fictional-12345"
    setup = client.put(route + "/portal", json={"url": manual + "?tracking=discard"}).raise_for_status().json()
    assert not setup["supported"] and setup["manual_url"] == manual
    assert client.get(route).raise_for_status().json()["manual_url"] == manual
    setup = client.put(route + "/portal", json={"url": "https://jobs.ashbyhq.com/fictional/42"}).raise_for_status().json()
    assert setup["supported"] and setup["provider"] == "ashby"
    assert setup["url"].endswith("/fictional/42/application")
    assert client.get(f"/applications/{app}").json()["source_url"] == original


def test_greenhouse_screening_uses_selected_resume_and_real_options(auto_client, job_data, profile_data):
    client, browser = auto_client
    profile_data["preferences"]["work_authorization"] = "Canadian Permanent Resident"
    client.put("/profile", json=profile_data).raise_for_status()
    app_id, initial = _application(client, job_data)
    content = json.loads(initial["content"])
    content["name"] = "Jordan Applicant (fictional)"
    content["contact"]["links"] = ["https://www.linkedin.com/in/fictional-applicant/"]
    selected = client.post(f"/documents/{initial['id']}/versions", json={"content": json.dumps(content)}).raise_for_status().json()
    route, setup = _setup(client, app_id)

    def q(label, field, kind="input_text", options=(), required=True):
        return {"label": label, "required": required,
                "fields": [{"name": field, "type": kind, "values": [{"label": x} for x in options]}]}

    metadata = {"location": {"name": "Toronto, Ontario, Canada"}, "questions": [
        q("What is your legal name?", "question_1"), q("Please share your LinkedIn profile URL", "question_2", required=False),
        q("Are you legally authorized to work in Canada?", "question_3", "multi_value_single_select", ("Yes", "No")),
        q("Will you require sponsorship to work in Canada?", "question_4", "multi_value_single_select", ("Yes", "No")),
        q("What is your current Canadian government security clearance status?", "question_5[]",
          "multi_value_multi_select", ("Secret", "No clearance / Ineligible")),
        q("Why are you interested in this role?", "question_6", required=False),
        q("On a scale of 1 to 10, rate your fit and explain why.", "question_7", required=False),
        q("Are you authorized to work in the United States?", "question_8", "multi_value_single_select", ("Yes", "No")),
        q("Are you authorized to work in Canada or Mexico?", "question_9", "multi_value_single_select", ("Yes", "No")),
        q("Are you currently legally authorized to work in the country where this position is located?",
          "question_10", "multi_value_single_select", ("Yes", "No")),
        q("Do you have any outside business activities?", "question_11"),
        q("If yes, please describe:", "question_12", required=False),
        q("Cover Letter", "cover_letter", "input_file", required=False),
    ]}
    client.app.state.fetch = lambda url: metadata
    assert setup["contact"]["full_name"] == content["name"]
    original_setup = client.get(route, params={"resume_id": initial["id"]}).raise_for_status().json()
    assert original_setup["preview"] != setup["preview"]
    prepared = client.get(route + "/screening", params={"resume_id": selected["id"]}).raise_for_status().json()
    name, linkedin, authorized, sponsor, clearance, why, fit, us, mixed, generic, outside, followup = prepared["questions"]
    assert prepared["cover_letter"] and name["suggested"] == content["name"]
    assert linkedin["suggested"] == content["contact"]["links"][0]
    assert authorized["suggested"] == "Yes" and sponsor["suggested"] == "No"
    assert us["suggested"] == mixed["suggested"] == generic["suggested"] == ""
    assert outside["manual"] and not outside["draftable"]
    assert not followup["draftable"]
    metadata["location"]["name"] = "Florida, United States; Toronto, Ontario, Canada"
    mixed_view = client.get(route + "/screening", params={"resume_id": selected["id"]}).raise_for_status().json()
    assert mixed_view["questions"][2]["suggested"] == mixed_view["questions"][3]["suggested"] == ""
    metadata["location"]["name"] = "Toronto, Ontario, Canada"
    assert clearance["suggested"] == "" and clearance["options"] == ["Secret", "No clearance / Ineligible"]
    assert why["draftable"] and not fit["draftable"] and not clearance["draftable"]
    approval = {"preview": setup["preview"], "resume_id": selected["id"], "screening_answers": {clearance["key"]: "Maybe"}}
    assert client.post(route, json=approval).status_code == 409
    approval["screening_answers"] = {clearance["key"]: "Secret", linkedin["key"]: ""}
    browser.mode = "pause"
    browser.release.set()
    client.post(route, json=approval).raise_for_status()
    _wait(client, route, "needs_you")
    assert browser.calls[0][3] == approval["screening_answers"]

    approval["screening_answers"] = {clearance["key"]: "No clearance / Ineligible"}
    browser.mode = "confirm"
    client.post(route, json=approval).raise_for_status()
    _wait(client, route, "confirmed")
    assert browser.calls[1][3] == approval["screening_answers"]


def test_only_direct_posting_hosts_and_stable_ids_are_accepted():
    gh = career_browser.target_for("https://boards.greenhouse.io/fictional/jobs/111?tracking=one")
    assert gh == career_browser.target_for("https://job-boards.greenhouse.io/fictional/jobs/111")
    assert not gh.on_posting("https://job-boards.greenhouse.io/fictional/jobs/1110")
    assert not gh.same_portal("https://job-boards.greenhouse.io.attacker.example/fictional/jobs/111")
    workday = career_browser.target_for("https://fictional.wd5.myworkdayjobs.com/en-US/Careers/job/City/Role_R4102/apply")
    assert workday.provider == "workday" and workday.key.endswith(":r4102")
    assert career_browser.target_for("https://jobs.lever.co/fictional/abc/apply").provider == "lever"
    assert career_browser.target_for("https://jobs.ashbyhq.com/fictional/abc/application").provider == "ashby"
    host = "jobs.lever.co"
    for url in ("http://jobs.lever.co/fictional/abc", f"https://user@{host}/fictional/abc",
                "https://jobs.lever.co:1234/fictional/abc", "https://jobs.lever.co.example.com/fictional/abc"):
        with pytest.raises(ConflictError):
            career_browser.target_for(url)
