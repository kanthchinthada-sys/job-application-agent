"""Assisted apply (Greenhouse): plan building, approval, duplicate guard and outcome recording.

GH_QUESTIONS mirrors the real public API response (?questions=true), checked live in Nov 2026.
"""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.errors import NotFoundError

GH_QUESTIONS = {
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Last Name", "required": True, "fields": [{"name": "last_name", "type": "input_text", "values": []}]},
        {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text", "values": []}]},
        {"label": "Phone", "required": True, "fields": [{"name": "phone", "type": "input_text", "values": []}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file", "values": []},
                                                           {"name": "resume_text", "type": "textarea", "values": []}]},
        {"label": "Cover Letter", "required": False, "fields": [{"name": "cover_letter", "type": "input_file", "values": []}]},
        {"label": "How did you hear about this job?", "required": True,
         "fields": [{"name": "question_1", "type": "input_text", "values": []}]},
        {"label": "LinkedIn Profile", "required": False, "fields": [{"name": "question_2", "type": "input_text", "values": []}]},
        {"label": "Do you now or will you in the future require immigration sponsorship?", "required": True,
         "fields": [{"name": "question_3", "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "Please review and acknowledge our Candidate Privacy Policy.", "required": True,
         "fields": [{"name": "question_4[]", "type": "multi_value_multi_select",
                     "values": [{"label": "Acknowledge/Confirm", "value": 99}]}]},
        {"label": "Why do you want to work here?", "required": False,
         "fields": [{"name": "question_5", "type": "textarea", "values": []}]},
    ],
    "location_questions": [{"label": "Latitude", "fields": [{"name": "latitude", "type": "input_hidden"}]},
                           {"label": "Location", "fields": [{"name": "location", "type": "input_text"}]}],
    "compliance": [{"type": "eeoc", "questions": [{"label": "Gender", "required": False, "fields": [
        {"name": "gender", "type": "multi_value_single_select", "values": [{"label": "Decline To Self Identify"}]}]}]}],
}
GH_URL = "https://boards.greenhouse.io/examplecorp/jobs/111"


def fetch(url):
    if url == "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs/111?questions=true":
        return GH_QUESTIONS
    raise NotFoundError("not found")


@pytest.fixture
def client(db_path, fake_llm, profile_data):
    profile_data["contact"].update({"phone": "+1 555 0100", "location": "Toronto, ON, Canada"})
    profile_data["preferences"]["work_authorization"] = "Canadian Permanent Resident (FICTIONAL)"
    with TestClient(create_app(db_path, llm=fake_llm, fetch=fetch)) as c:
        c.put("/profile", json=profile_data)
        yield c


@pytest.fixture
def app_id(client, job_data):
    job = client.post("/jobs", json={**job_data, "source_url": GH_URL, "location": "Toronto, ON"}).json()
    return client.post("/applications", json={"job_id": job["id"]}).json()["id"]


def plan_fields(client, app_id):
    import json
    doc = client.post(f"/applications/{app_id}/submission/plan").json()
    return {f["name"]: f for f in json.loads(doc["content"])["fields"]}, doc


def test_plan_proposes_answers_and_marks_what_needs_you(client, app_id):
    f, doc = plan_fields(client, app_id)
    assert doc["kind"] == "application_plan" and doc["source"] == "agent"
    assert f["first_name"]["answer"] == "Jordan" and f["last_name"]["answer"] == "Sample (FICTIONAL TEST DATA)"
    assert f["phone"]["answer"] == "+1 555 0100" and f["location"]["answer"] == "Toronto, ON, Canada"
    assert f["country"]["answer"] == "Canada"
    assert f["resume"]["source"] == "file" and "resume_text" not in f and "latitude" not in f
    assert f["question_1"]["needs_you"] and f["question_1"]["answer"] == ""            # how did you hear: only you know
    assert f["question_2"]["answer"].startswith("https://example.com")                    # LinkedIn / links
    assert f["question_3"]["answer"] == "No" and f["question_3"]["sensitive"]            # sponsorship, job in Canada
    assert f["question_4[]"]["needs_you"] and f["question_4[]"]["answer"] == ""          # consent: never auto
    assert f["gender"]["sensitive"] and f["gender"]["answer"] == "" and not f["gender"]["needs_you"]
    assert f["question_5"]["needs_you"]                                                  # custom question, no draft yet


def test_sponsorship_outside_canada_needs_you(client, job_data):
    job = client.post("/jobs", json={**job_data, "source_url": GH_URL, "location": "Washington DC, US"}).json()
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    f, doc = plan_fields(client, app_id)
    assert f["question_3"]["needs_you"] and f["question_3"]["answer"] == ""
    assert any("may not be in Canada" in w for w in doc["review"]["warnings"])


def test_drafted_screening_answer_is_used(client, fake_llm, app_id):
    fake_llm.answer = {"text": "Because I build pytest suites for payments APIs.", "fact_ids_used": ["F4"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "screening_answer", "question": "Why do you want to work here?"})
    f, _ = plan_fields(client, app_id)
    assert f["question_5"]["source"] == "document" and "pytest" in f["question_5"]["answer"]


def test_approval_requires_your_answers_and_valid_options(client, app_id):
    plan_fields(client, app_id)
    r = client.post(f"/applications/{app_id}/submission/approve", json={"answers": {}})
    assert r.status_code == 409 and "How did you hear" in r.json()["detail"]
    r = client.post(f"/applications/{app_id}/submission/approve",
                    json={"answers": {"question_1": "Company website", "question_4[]": "I agree"}})
    assert r.status_code == 409 and "not one of the options" in r.json()["detail"]
    r = client.post(f"/applications/{app_id}/submission/approve",
                    json={"answers": {"question_1": "Company website", "question_4[]": "Acknowledge/Confirm"}})
    assert r.status_code == 200 and '"approved":true' in r.json()["content"]
    assert client.get(f"/applications/{app_id}").json()["status"] == "ready_for_review"
    ready = client.get(f"/applications/{app_id}/submission/ready").json()
    assert ready["plan"]["approved"] and ready["has_resume"] is False


def test_ready_requires_approved_plan(client, app_id):
    assert client.get(f"/applications/{app_id}/submission/ready").status_code == 404
    plan_fields(client, app_id)
    r = client.get(f"/applications/{app_id}/submission/ready")
    assert r.status_code == 409 and "isn't approved" in r.json()["detail"]


def _approve(client, app_id):
    plan_fields(client, app_id)
    client.post(f"/applications/{app_id}/submission/approve",
                json={"answers": {"question_1": "Company website", "question_4[]": "Acknowledge/Confirm"}})


def test_outcomes_map_to_honest_statuses(client, app_id):
    _approve(client, app_id)
    filled = client.post(f"/applications/{app_id}/submission/result", json={"outcome": "filled", "details": "12 fields"}).json()
    assert filled["status"] == "ready_for_review"                                 # a filled form is NOT a submission
    assert filled["history"][-1]["note"] == "Assisted apply: filled (12 fields)"
    assert client.post(f"/applications/{app_id}/submission/result", json={"outcome": "confirmed"}).status_code == 409
    done = client.post(f"/applications/{app_id}/submission/result",
                       json={"outcome": "confirmed", "evidence": "Confirmation page https://x/confirmation"}).json()
    assert done["status"] == "submitted_confirmed" and done["submission_confirmation"].startswith("Confirmation page")
    assert [h["to_status"] for h in done["history"]][-2:] == ["submission_attempted", "submitted_confirmed"]


def test_uncertain_outcome_and_duplicate_guard(client, app_id):
    _approve(client, app_id)
    r = client.post(f"/applications/{app_id}/submission/result", json={"outcome": "uncertain", "details": "no confirmation"})
    assert r.json()["status"] == "submission_uncertain"
    again = client.get(f"/applications/{app_id}/submission/ready")
    assert again.status_code == 409 and "uncertain" in again.json()["detail"]
    assert client.post(f"/applications/{app_id}/submission/plan").status_code == 409


def test_already_submitted_is_never_refilled(client, app_id):
    _approve(client, app_id)
    client.post(f"/applications/{app_id}/submission/result", json={"outcome": "confirmed", "evidence": "page"})
    r = client.get(f"/applications/{app_id}/submission/ready")
    assert r.status_code == 409 and "won't apply twice" in r.json()["detail"]


def test_non_greenhouse_job_is_rejected(client, job_data):
    job = client.post("/jobs", json={**job_data, "source_url": "https://jobs.lever.co/acme/1"}).json()
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    r = client.post(f"/applications/{app_id}/submission/plan")
    assert r.status_code == 409 and "Greenhouse" in r.json()["detail"]


def test_cover_letter_page(client, fake_llm, app_id):
    assert client.get(f"/applications/{app_id}/cover-letter").status_code == 404
    fake_llm.answer = {"text": "I build pytest suites.", "fact_ids_used": ["F4"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"})
    page = client.get(f"/applications/{app_id}/cover-letter")
    assert page.status_code == 200 and "Dear Hiring Team," in page.text and "Times New Roman" in page.text


def test_plan_kind_cannot_be_drafted_directly(client, app_id):
    assert client.post(f"/applications/{app_id}/drafts", json={"kind": "application_plan"}).status_code == 422
