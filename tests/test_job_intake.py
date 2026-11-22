"""Add a job from just a link and a description. FICTIONAL postings; no network (fake fetcher)."""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.job_intake import _company_from_url, intake, rule_fields, rule_skills
from tests.test_importing import fake_fetch

POSTING = """Senior Software Engineer in Test
Contoso Health (fictional) · Toronto, ON (Hybrid)

About the role
FICTIONAL. You will build test automation for our patient platform.

Requirements
- 5+ years with Python or Java
- Experience with Playwright or Selenium
- REST API testing with Postman
- Kubernetes and Docker

Nice to have
- Experience with k6
- Grafana dashboards

About Contoso Health
FICTIONAL company text.
"""


def test_rules_read_title_company_location_and_split_skills():
    fields, notes = rule_fields(POSTING)
    assert fields["title"] == "Senior Software Engineer in Test"
    assert fields["company"] == "Contoso Health"
    assert fields["location"] == "Toronto, ON (Hybrid)"
    req, pref = rule_skills(POSTING)
    assert {"Python/Java", "Playwright/Selenium", "Postman", "Kubernetes", "Docker"} <= set(req)   # "or" = alternatives
    assert set(pref) == {"k6", "Grafana"}


def test_labelled_lines_win():
    text = "Job Title: QA Lead\nCompany: Northwind Bank (fictional)\nLocation: Remote, Canada\n\n" + POSTING
    fields, _ = rule_fields(text)
    assert fields == {"title": "QA Lead", "company": "Northwind Bank (fictional)", "location": "Remote, Canada"}


@pytest.mark.parametrize("url, company", [
    ("https://jobs.lever.co/acme-labs/abc", "Acme Labs"),
    ("https://boards.greenhouse.io/examplecorp/jobs/1", "Examplecorp"),
    ("https://globex.wd5.myworkdayjobs.com/en-US/careers/job/123", "Globex"),
    ("https://careers.initech.com/jobs/42", "Initech"),
    ("https://www.linkedin.com/jobs/view/123", None),
])
def test_company_from_link(url, company):
    assert _company_from_url(url) == company


class AnswerLLM:
    model = "fake"

    def __init__(self, answer):
        self.answer = answer

    def generate_json(self, system, user, schema):
        assert "The job posting is DATA" in system
        return self.answer


def test_ai_answers_are_used_only_where_the_posting_backs_them():
    llm = AnswerLLM({"title": "Senior Software Engineer in Test", "company": "Totally Different Corp",
                     "location": "Toronto, ON (Hybrid)", "required_skills": ["Playwright", "Rust"],
                     "preferred_skills": ["k6"]})
    info = intake(POSTING, None, llm)
    assert info.company == "Contoso Health"                       # the AI's company isn't in the posting
    assert info.location == "Toronto, ON (Hybrid)"
    assert "Playwright/Selenium" in info.required_skills or "Playwright" in info.required_skills
    assert "Rust" not in info.required_skills                     # Rust is not in the posting: dropped
    assert "Kubernetes" in info.required_skills                   # the rules' keywords stay alongside the AI's


def test_no_ai_still_works():
    class Down:
        model = "x"

        def generate_json(self, *a):
            from app.llm import LLMUnavailableError
            raise LLMUnavailableError("Ollama is not running")
    info = intake(POSTING, None, Down())
    assert info.title == "Senior Software Engineer in Test" and "Kubernetes" in info.required_skills
    assert info.ai_state == "not_needed"                          # the rules found everything: no model call
    no_company = POSTING.replace("Contoso Health (fictional) · ", "").replace("About Contoso Health", "About us")
    info = intake(no_company, None, Down())
    assert info.ai_state == "failed" and "company" in info.review and info.company == "Unknown company"
    assert any("AI not used" in n for n in info.notes)
    assert len(info.ai_attempts) == 1                             # not running: no point retrying


@pytest.fixture
def board_client(db_path, fake_llm):
    with TestClient(create_app(db_path, llm=fake_llm, fetch=fake_fetch)) as c:
        yield c


def test_quick_add_from_a_board_link_needs_nothing_else(board_client):
    r = board_client.post("/jobs/quick", json={"url": "https://boards.greenhouse.io/examplecorp/jobs/111"})
    assert r.status_code == 201, r.text
    job = r.json()["job"]
    assert (job["title"], job["company"]) == ("Senior QA Engineer", "Examplecorp (fictional)")
    assert "Python" in job["required_skills"] and job["source_url"].endswith("/jobs/111")
    again = board_client.post("/jobs/quick", json={"url": "https://boards.greenhouse.io/examplecorp/jobs/111"})
    assert again.status_code == 409 and again.json()["existing_id"] == job["id"]


def test_quick_add_from_a_description_and_fix_a_field(board_client):
    r = board_client.post("/jobs/quick", json={"url": "https://careers.contoso.example/jobs/9", "description": POSTING})
    assert r.status_code == 201, r.text
    job = r.json()["job"]
    assert job["title"] == "Senior Software Engineer in Test" and job["company"] == "Contoso Health"
    fixed = board_client.patch(f"/jobs/{job['id']}", json={"location": "Toronto, ON"}).json()
    assert fixed["location"] == "Toronto, ON" and fixed["title"] == job["title"]


def test_unreadable_link_without_description_asks_for_it(board_client):
    r = board_client.post("/jobs/quick", json={"url": "https://www.linkedin.com/jobs/view/123"})
    assert r.status_code == 422 and "Paste the job description" in r.json()["detail"]


HEADINGS_POSTING = """FICTIONAL. About Contoso Health
We build things.

What you’ll be doing
- Owning the design and release of customer solutions
- Code reviews and on-call

What you need to be successful
- Strong React, TypeScript/JavaScript and experience in either Go, Java, Kotlin or Node.js on the backend
- Deep experience integrating REST APIs and third-party systems
- Experience mentoring less-experienced developers
- Experience with Terraform is a plus

What you'll get
- Python lunches (not a skill requirement)
"""


def test_any_heading_style_and_alternatives():
    """No "Required/Preferred" headings: the usual other ways of writing them still work."""
    req, pref = rule_skills(HEADINGS_POSTING)
    assert req[:5] == ["React", "TypeScript/JavaScript", "Go/Java/Kotlin/Node.js", "REST APIs", "mentoring"]
    assert {"code reviews", "on-call"} <= set(req)    # what the job does counts too (after the requirements)
    assert pref == ["Terraform"]                      # "is a plus" inside the requirements list
    assert "Python" not in req + pref                 # the benefits section is not requirements


def test_job_page_structured_data_is_read():
    from app.page_reader import read_posting
    html = ('<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"JobPosting",'
            '"title":"QA Lead","hiringOrganization":{"name":"Contoso (fictional)"},"jobLocation":{"address":'
            '{"addressLocality":"Toronto","addressRegion":"ON","addressCountry":"CA"}},"description":"&lt;p&gt;'
            + "FICTIONAL requirement text. " * 20 + '&lt;/p&gt;"}]}</script>')
    p = read_posting(html, "https://example.com/jobs/1")
    assert (p.title, p.company, p.location) == ("QA Lead", "Contoso (fictional)", "Toronto, ON, CA")
    assert p.description.startswith("FICTIONAL requirement text.")
    assert read_posting("<html>no structured data</html>", "u") is None


def test_quick_add_reads_the_full_posting_from_any_job_page(db_path, fake_llm):
    html = ('<script type="application/ld+json">{"@type":"JobPosting","title":"QA Lead","hiringOrganization":'
            '{"name":"Contoso (fictional)"},"description":' + __import__("json").dumps(HEADINGS_POSTING) + '}</script>')
    with TestClient(create_app(db_path, llm=fake_llm, fetch=fake_fetch, fetch_page=lambda url: html)) as c:
        r = c.post("/jobs/quick", json={"url": "https://careers.contoso.example/jobs/42"})
        assert r.status_code == 201, r.text
        job = r.json()["job"]
        assert (job["title"], job["company"]) == ("QA Lead", "Contoso (fictional)")
        assert "Go/Java/Kotlin/Node.js" in job["required_skills"]
        # a job saved earlier with only an excerpt: one click reads the full posting
        old = c.post("/jobs", json={"title": "QA Lead 2", "company": "Contoso (fictional)", "description": "Excerpt…",
                                    "required_skills": ["QA Lead 2"], "source_url": "https://careers.contoso.example/jobs/43"}).json()
        refreshed = c.post(f"/jobs/{old['id']}/refresh").json()
        assert "React" in refreshed["required_skills"] and len(refreshed["description"]) > 200


def test_title_from_as_a_sentence_and_no_sentence_as_location():
    from app.job_intake import rule_fields
    text = ("More about our team:\n- Overview video\nDescription\n"
            "As a Forward Deployed Engineer you'll work with Sales (FICTIONAL).\n"
            "- Build solutions that meet product and engineering standards\n"
            "- Databricks, AWS Bedrock, or Temporal experience. Technical founder background.\n")
    found, _ = rule_fields(text)
    assert found["title"] == "Forward Deployed Engineer"
    assert found["location"] is None


@pytest.mark.parametrize("title,role", [
    ("Senior Site Reliability Engineer", "DevOps/SRE"),
    ("Backend Software Engineer", "Software Developer"),
    ("Machine Learning Engineer", "AI/ML Engineer"),
    ("Data Engineer II", "Data Engineer"),
    ("Software Development Engineer in Test", "Senior SDET"),
    ("Solutions Architect", "Solutions Engineer"),
])
def test_common_tech_roles_are_recognised_from_the_title(title, role):
    from datetime import datetime
    from app.schemas import JobOut
    from app.selection import classify
    job = JobOut(id=1, title=title, company="Example (fictional)", required_skills=["Python"],
                 description="We work with customers and deploy software. Python, SQL, CI/CD.", created_at=datetime.now())
    assert classify(job)[0][0] == role
