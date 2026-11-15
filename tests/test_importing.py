"""Job import from Greenhouse / Lever / Ashby and preference checks. No network: a fake fetcher
returns responses shaped like the real public APIs (checked against live responses in Nov 2026)."""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.importers import html_to_text, parse_link
from app.preferences import check_location, check_title

GH_JOB = {
    "id": 111, "title": "Senior QA Engineer ", "company_name": "Examplecorp (fictional)",
    "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/111",
    "location": {"name": "Hybrid"}, "offices": [{"location": "Toronto, ON, Canada", "name": "Toronto"}],
    "metadata": [{"name": "Job Posting Location", "value": ["Toronto, Canada"]}],
    "content": "&lt;h3&gt;About&lt;/h3&gt;&lt;p&gt;FICTIONAL. We test things.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;Playwright&lt;/li&gt;&lt;/ul&gt;",
}
GH_BOARD = {"jobs": [
    {**GH_JOB, "content": None},
    {"id": 112, "title": "Account Executive", "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/112",
     "location": {"name": "Toronto, Canada"}},
    {"id": 113, "title": "SDET", "absolute_url": "https://boards.greenhouse.io/examplecorp/jobs/113",
     "location": {"name": "San Francisco, CA"}},
], "meta": {}}
LEVER_JOB = {
    "id": "abc-123", "text": "Test Automation Engineer", "hostedUrl": "https://jobs.lever.co/acme-labs/abc-123",
    "categories": {"allLocations": ["Remote (Canada)"], "commitment": "Contract", "location": "Remote (Canada)"},
    "workplaceType": "remote", "descriptionPlain": "FICTIONAL posting.",
    "lists": [{"text": "Requirements", "content": "<li>Selenium</li><li>Java</li>"}], "additionalPlain": "Apply soon.",
}
ASHBY_BOARD = {"jobs": [{
    "id": "u-1", "title": "Forward Deployed Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/u-1",
    "location": "New York, NY", "secondaryLocations": [{"location": "Remote (Canada)"}], "isRemote": True,
    "isListed": True, "employmentType": "FullTime", "descriptionPlain": "FICTIONAL. Work with customers.",
}]}

RESPONSES = {
    "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs/111": GH_JOB,
    "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs": GH_BOARD,
    "https://api.lever.co/v0/postings/acme-labs/abc-123?mode=json": LEVER_JOB,
    "https://api.lever.co/v0/postings/acme-labs?mode=json": [LEVER_JOB],
    "https://api.ashbyhq.com/posting-api/job-board/acme": ASHBY_BOARD,
}


def fake_fetch(url):
    if url not in RESPONSES:
        from app.errors import NotFoundError
        raise NotFoundError("not found")
    return RESPONSES[url]


@pytest.fixture
def client(db_path, fake_llm, profile_data):
    with TestClient(create_app(db_path, llm=fake_llm, fetch=fake_fetch)) as c:
        profile_data["preferences"] = {"target_roles": ["QA Engineer", "SDET", "Forward Deployed Engineer",
                                                        "Test Automation Engineer"], "locations": ["Canada"]}
        c.put("/profile", json=profile_data)
        yield c


# ---- pure helpers ----

@pytest.mark.parametrize("url, provider, board, job_id", [
    ("https://boards.greenhouse.io/examplecorp/jobs/111", "greenhouse", "examplecorp", "111"),
    ("job-boards.greenhouse.io/examplecorp/jobs/111?gh_jid=111", "greenhouse", "examplecorp", "111"),
    ("https://jobs.lever.co/acme-labs/abc-123/apply", "lever", "acme-labs", "abc-123"),
    ("https://jobs.lever.co/acme-labs", "lever", "acme-labs", None),
    ("https://jobs.ashbyhq.com/acme/u-1", "ashby", "acme", "u-1"),
])
def test_parse_link(url, provider, board, job_id):
    link = parse_link(url)
    assert (link.provider, link.board, link.job_id) == (provider, board, job_id)


def test_unsupported_link(client):
    response = client.post("/import/job", json={"url": "https://www.linkedin.com/jobs/view/123"})
    assert response.status_code == 409 and "paste the description" in response.json()["detail"]


def test_html_to_text_handles_escaped_html():
    assert html_to_text(GH_JOB["content"]) == "About\n\nFICTIONAL. We test things.\n\n- Python\n\n- Playwright"


@pytest.mark.parametrize("title, roles, expected", [
    ("Senior Quality Assurance Engineer", ["QA Engineer"], "strong"),
    ("Software Development Engineer in Test II", ["SDET"], "strong"),
    ("Sr. QA Lead", ["QA Lead"], "strong"),
    ("QA Automation Lead", ["QA Lead"], "strong"),
    ("Account Executive", ["QA Lead", "SDET"], "none"),
    ("Quality Engineer, Platform", ["Senior Quality Engineer"], "strong"),
])
def test_check_title(title, roles, expected):
    assert check_title(title, roles)[0] == expected


@pytest.mark.parametrize("location, expected", [
    ("Toronto, ON", "yes"), ("Remote (Canada)", "yes"), ("Brampton, Ontario", "yes"),
    ("San Francisco, CA", "no"), ("Remote", "unclear"), ("Remote (US)", "no"), (None, "unclear"),
])
def test_check_location(location, expected):
    assert check_location(location, ["Canada"])[0] == expected


# ---- endpoints ----

def test_import_greenhouse_job(client):
    body = client.post("/import/job", json={"url": "https://boards.greenhouse.io/examplecorp/jobs/111"}).json()
    job = body["job"]
    assert job["title"] == "Senior QA Engineer" and job["company"] == "Examplecorp (fictional)"
    assert "Toronto" in job["location"] and "- Playwright" in job["description"]
    assert body["preferences"]["title_match"] == "strong" and body["preferences"]["location_match"] == "yes"
    assert body["already_added_job_id"] is None
    assert client.get("/jobs").json() == []   # preview never saves


def test_import_lever_job(client):
    job = client.post("/import/job", json={"url": "https://jobs.lever.co/acme-labs/abc-123"}).json()["job"]
    assert job["company"] == "Acme Labs" and job["employment_type"] == "Contract"
    assert "Requirements\n- Selenium" in job["description"] and job["location"] == "Remote (Canada) (remote)"


def test_import_ashby_job(client):
    body = client.post("/import/job", json={"url": "https://jobs.ashbyhq.com/acme/u-1"}).json()
    assert body["job"]["title"] == "Forward Deployed Engineer"
    assert body["preferences"]["location_match"] == "yes"  # via the Remote (Canada) secondary location


def test_already_added_is_reported(client):
    preview = client.post("/import/job", json={"url": "https://boards.greenhouse.io/examplecorp/jobs/111"}).json()["job"]
    saved = client.post("/jobs", json={"title": preview["title"], "company": preview["company"],
                                       "source_url": preview["source_url"], "description": preview["description"],
                                       "required_skills": ["Python"]}).json()
    again = client.post("/import/job", json={"url": "https://boards.greenhouse.io/examplecorp/jobs/111"}).json()
    assert again["already_added_job_id"] == saved["id"]


def test_missing_job_returns_404(client):
    assert client.post("/import/job", json={"url": "https://boards.greenhouse.io/examplecorp/jobs/999"}).status_code == 404
    assert client.post("/import/job", json={"url": "https://jobs.ashbyhq.com/acme/nope"}).status_code == 404


def test_board_link_without_job_id_needs_board_search(client):
    response = client.post("/import/job", json={"url": "https://jobs.lever.co/acme-labs"})
    assert response.status_code == 409 and "board" in response.json()["detail"]


def test_board_search_filters_by_preferences(client):
    body = client.post("/import/board", json={"url": "https://boards.greenhouse.io/examplecorp"}).json()
    assert body["total_postings"] == 3
    titles = [r["posting"]["title"] for r in body["results"]]
    assert titles == ["Senior QA Engineer"]   # Account Executive: wrong role; SDET: in San Francisco
    everything = client.post("/import/board", json={"url": "https://boards.greenhouse.io/examplecorp",
                                                    "only_suitable": False}).json()
    assert everything["shown"] == 3


def test_board_search_without_profile_shows_all(db_path, fake_llm):
    with TestClient(create_app(db_path, llm=fake_llm, fetch=fake_fetch)) as c:
        assert c.post("/import/board", json={"url": "https://jobs.lever.co/acme-labs"}).json()["shown"] == 1


def test_greenhouse_job_seeker_and_embedded_links():
    from app.importers import parse_link
    link = parse_link("https://my.greenhouse.io/jobs/examplecorp/111?country_short_name=CA&from_recommendation=true"
                      "&location=Canada&query=software+engineer")
    assert (link.provider, link.board, link.job_id) == ("greenhouse", "examplecorp", "111")
    link = parse_link("https://boards.greenhouse.io/embed/job_app?for=examplecorp&token=111")
    assert (link.provider, link.board, link.job_id) == ("greenhouse", "examplecorp", "111")
