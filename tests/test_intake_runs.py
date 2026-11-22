"""Adding a job never waits for the model; the model fills only what's missing and never overwrites your edits.
All postings FICTIONAL; no network (fake page fetcher, fake model)."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.llm import LLMTimeoutError

STATED = """Senior QA Automation Engineer
Northwind Payments (fictional) - Toronto, ON (Hybrid)

About the role
Northwind Payments is hiring a Senior QA Automation Engineer to own test automation.

Requirements
- Strong Python and pytest experience for REST API testing.
- Experience with Playwright or Selenium for UI testing.
- SQL skills for data validation.
"""

NO_COMPANY = """Senior Quality Engineer (fictional)
Remote, Canada

About the role
You will own test strategy for a growing platform. You'll pair with Tailspin Toys (fictional) engineers daily.

What we're looking for
- Experience with Cypress, Postman and SQL.
- Familiarity with AWS is a plus.
"""

PAGE = ('<script type="application/ld+json">' + json.dumps({
    "@type": "JobPosting", "title": "Platform Test Engineer (fictional)",
    "hiringOrganization": {"name": "Fabrikam Robotics (fictional)"},
    "jobLocation": {"address": {"addressLocality": "Ottawa", "addressRegion": "ON"}},
    "description": "Requirements: Playwright, TypeScript, k6, Docker and Terraform. " * 6}) + "</script>")


class SlowLLM:
    """A model that answers only when told to (or fails / times out as set)."""
    model = "fake"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer or {}, error, []
        self.release = threading.Event()
        self.release.set()

    def generate_json(self, system, user, schema, timeout=None, max_tokens=None):
        self.calls.append({"timeout": timeout, "max_tokens": max_tokens, "schema": schema})
        self.release.wait(5)
        if self.error:
            raise self.error
        return self.answer

    def status(self):
        return {"reachable": True}


def client_for(db_path, llm, page=PAGE):
    return TestClient(create_app(db_path, llm=llm, fetch_page=lambda url: page))


def test_structured_page_data_needs_no_model(db_path):
    llm = SlowLLM()
    with client_for(db_path, llm) as c:
        r = c.post("/jobs/quick", json={"url": "https://careers.fabrikam.example/jobs/1"}).json()
        assert (r["job"]["title"], r["job"]["company"]) == ("Platform Test Engineer (fictional)",
                                                            "Fabrikam Robotics (fictional)")
        assert r["intake"]["ai_state"] == "not_needed" and r["intake"]["review"] == []
        assert llm.calls == []


def test_a_clearly_stated_company_needs_no_model(db_path):
    llm = SlowLLM()
    with client_for(db_path, llm) as c:
        r = c.post("/jobs/quick", json={"description": STATED}).json()
        assert r["job"]["company"] == "Northwind Payments (fictional)"
        assert r["job"]["location"] == "Toronto, ON (Hybrid)"
        assert r["intake"]["ai_state"] == "not_needed" and llm.calls == []


def test_missing_company_is_saved_at_once_flagged_and_filled_only_where_the_posting_backs_it(db_path):
    llm = SlowLLM({"title": "Senior Quality Engineer (fictional)", "company": "Tailspin Toys (fictional)",
                   "location": "Remote, Canada", "required_skills": ["Cypress"], "preferred_skills": []})
    llm.release.clear()                                      # the model is slow: the job is saved without it
    with client_for(db_path, llm) as c:
        r = c.post("/jobs/quick", json={"description": NO_COMPANY}).json()
        job_id = r["job"]["id"]
        assert r["intake"]["ai_state"] == "pending" and "company" in r["intake"]["review"]
        assert r["job"]["company"] == "Unknown company" and r["job"]["description"].startswith("Senior Quality")
        llm.release.set()
        assert c.app.state.intake.wait(job_id)
        s = c.get(f"/jobs/{job_id}/intake").json()
        assert s["ai_state"] == "done" and s["applied"] == ["company"] and s["review"] == []
        assert s["job"]["company"] == "Tailspin Toys (fictional)"
        assert llm.calls[0]["max_tokens"] and llm.calls[0]["timeout"]
        assert llm.calls[0]["schema"]["properties"]["preferred_skills"]["maxItems"]   # no endless lists


def test_an_unbacked_company_from_the_model_is_not_used(db_path):
    llm = SlowLLM({"company": "Globex (fictional)"})
    with client_for(db_path, llm) as c:
        job_id = c.post("/jobs/quick", json={"description": NO_COMPANY}).json()["job"]["id"]
        c.app.state.intake.wait(job_id)
        s = c.get(f"/jobs/{job_id}/intake").json()
        assert s["job"]["company"] == "Unknown company" and "company" in s["review"]


def test_a_late_model_answer_never_overwrites_your_correction(db_path):
    llm = SlowLLM({"company": "Tailspin Toys (fictional)"})
    llm.release.clear()
    with client_for(db_path, llm) as c:
        job_id = c.post("/jobs/quick", json={"description": NO_COMPANY}).json()["job"]["id"]
        assert c.patch(f"/jobs/{job_id}", json={"company": "Wingtip (fictional)"}).status_code == 200
        llm.release.set()                                    # the model answers after you corrected it
        c.app.state.intake.wait(job_id)
        s = c.get(f"/jobs/{job_id}/intake").json()
        assert s["job"]["company"] == "Wingtip (fictional)"
        assert s["kept"] == ["company"] and s["applied"] == []


def test_a_slow_model_times_out_retries_once_and_keeps_everything(db_path, monkeypatch):
    monkeypatch.setenv("JOB_INTAKE_AI_TIMEOUT", "7")
    monkeypatch.setenv("JOB_INTAKE_AI_RETRIES", "1")
    llm = SlowLLM(error=LLMTimeoutError("took longer than 7 seconds"))
    with client_for(db_path, llm, page="<html>no posting data</html>") as c:
        r = c.post("/jobs/quick", json={"description": NO_COMPANY, "url": "https://example.com/fictional/9"}).json()
        job_id = r["job"]["id"]
        c.app.state.intake.wait(job_id)
        s = c.get(f"/jobs/{job_id}/intake").json()
        assert s["ai_state"] == "failed" and [a["outcome"] for a in s["attempts"]] == ["timeout", "timeout"]
        assert [x["timeout"] for x in llm.calls] == [7.0, 7.0]
        assert "company" in s["review"] and any("did not answer in time" in n for n in s["notes"])
        assert s["job"]["description"] == NO_COMPANY.strip() and s["job"]["source_url"] == "https://example.com/fictional/9"
        assert "Cypress" in s["job"]["required_skills"]                # what the rules read stays


def test_the_company_you_type_is_kept(db_path):
    llm = SlowLLM()
    with client_for(db_path, llm) as c:
        r = c.post("/jobs/quick", json={"description": STATED, "company": "Northwind Group (fictional)"}).json()
        assert r["job"]["company"] == "Northwind Group (fictional)" and r["intake"]["found_by"]["company"] == "you"


@pytest.mark.parametrize("url, company", [
    ("https://ca.linkedin.com/jobs/view/forward-deployed-engineer-at-contoso-labs-4492737723", "Contoso Labs"),
    ("https://www.linkedin.com/jobs/view/4492737723", None),
])
def test_company_from_a_linkedin_link(url, company):
    from app.job_intake import _company_from_url
    assert _company_from_url(url) == company


def test_page_metadata_names_the_company_when_there_is_no_structured_posting(db_path):
    page = ('<meta property="og:title" content="Contoso (fictional) hiring QA Lead in Toronto, ON | LinkedIn">'
            '<meta property="og:site_name" content="LinkedIn">')
    llm = SlowLLM()
    with client_for(db_path, llm, page) as c:
        desc = NO_COMPANY.replace("Tailspin Toys (fictional)", "the platform")
        r = c.post("/jobs/quick", json={"description": desc, "url": "https://www.linkedin.com/jobs/view/1"}).json()
        assert r["job"]["company"] == "Contoso (fictional)" and r["intake"]["found_by"]["company"] == "job page"
        assert r["intake"]["ai_state"] == "not_needed"


def test_a_restart_during_the_model_step_shows_it_failed_and_keeps_the_fields(db_path):
    with client_for(db_path, SlowLLM()) as c:
        job = c.post("/jobs", json={"title": "Senior Quality Engineer (fictional)", "company": "Unknown company",
                                    "description": NO_COMPANY, "required_skills": ["Cypress"]}).json()
    from app import database
    conn = database.connect(db_path)          # the app stopped while the model was still reading this posting
    conn.execute("INSERT INTO intake_runs (job_id, data, updated_at) VALUES (?, ?, 'x')", (job["id"], json.dumps(
        {"ai_state": "pending", "missing": ["company"], "review": ["company"], "notes": [], "found_by": {},
         "saved": {}, "user_fields": [], "applied": [], "kept": [], "attempts": []})))
    conn.commit()
    conn.close()
    with client_for(db_path, SlowLLM()) as c:
        s = c.get(f"/jobs/{job['id']}/intake").json()
        assert s["ai_state"] == "failed" and any("restarted" in n for n in s["notes"])
        assert s["review"] == ["company"] and s["job"]["description"].startswith("Senior Quality")


def test_our_client_is_never_taken_as_the_company(db_path):
    """A real run: the model answered "Our client" (backed by "Our client is a fast-growing software company")."""
    desc = NO_COMPANY.replace("You'll pair with Tailspin Toys (fictional) engineers daily.",
                              "Our client is a fast-growing software company.")
    with client_for(db_path, SlowLLM({"company": "Our client"})) as c:
        job_id = c.post("/jobs/quick", json={"description": desc}).json()["job"]["id"]
        c.app.state.intake.wait(job_id)
        s = c.get(f"/jobs/{job_id}/intake").json()
        assert s["job"]["company"] == "Unknown company" and "company" in s["review"]


def test_the_model_agreeing_with_the_rules_clears_the_flag(db_path):
    desc = NO_COMPANY.replace("You'll pair with Tailspin Toys (fictional) engineers daily.",
                              "You will join Fabrikam Robotics, we build robots (fictional).")
    with client_for(db_path, SlowLLM({"company": "Fabrikam Robotics"})) as c:
        r = c.post("/jobs/quick", json={"description": desc}).json()
        assert r["job"]["company"] == "Fabrikam Robotics" and r["intake"]["review"] == ["company"]   # from a phrase
        c.app.state.intake.wait(r["job"]["id"])
        s = c.get(f"/jobs/{r['job']['id']}/intake").json()
        assert s["review"] == [] and not any(n.startswith("Company read from a phrase") for n in s["notes"])


def test_a_company_from_structured_data_has_no_note_about_guessing_it(db_path):
    with client_for(db_path, SlowLLM()) as c:
        r = c.post("/jobs/quick", json={"url": "https://careers.fabrikam.example/jobs/1"}).json()
        assert not any("taken from the link" in n for n in r["intake"]["notes"])
