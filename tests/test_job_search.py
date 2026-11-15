"""Find jobs: title, country and date range across job sources. FICTIONAL postings, no network: a fake
fetcher returns responses shaped like each source's public API."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import job_search
from app.api import create_app
from app.job_search import COUNTRIES, where_for

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()                       # noqa: E731

REMOTIVE = {"job-count": 4, "jobs": [
    {"id": 1, "url": "https://remotive.example/jobs/1", "title": "Senior QA Engineer", "company_name": "Contoso (fictional)",
     "publication_date": D(1) + "T10:00:00", "candidate_required_location": "Canada", "description": "<p>FICTIONAL. Python.</p>"},
    {"id": 2, "url": "https://remotive.example/jobs/2", "title": "QA Engineer", "company_name": "Globex (fictional)",
     "publication_date": D(2) + "T10:00:00", "candidate_required_location": "Worldwide", "description": "FICTIONAL."},
    {"id": 3, "url": "https://remotive.example/jobs/3", "title": "QA Engineer", "company_name": "Initech (fictional)",
     "publication_date": D(2) + "T10:00:00", "candidate_required_location": "Europe only", "description": "FICTIONAL."},
    {"id": 4, "url": "https://remotive.example/jobs/4", "title": "Account Executive", "company_name": "Umbrella (fictional)",
     "publication_date": D(1) + "T10:00:00", "candidate_required_location": "Worldwide", "description": "FICTIONAL."},
]}
JOBICY = {"jobs": [
    {"id": 10, "url": "https://jobicy.example/jobs/10", "jobTitle": "Quality Assurance Engineer",
     "companyName": "Hooli (fictional)", "jobGeo": "Canada", "pubDate": D(3) + " 08:00:00",
     "jobExcerpt": "FICTIONAL excerpt.", "jobDescription": "<p>FICTIONAL full text.</p>"},
    {"id": 11, "url": "https://jobicy.example/jobs/11", "jobTitle": "QA Engineer",
     "companyName": "Old Corp (fictional)", "jobGeo": "Canada", "pubDate": D(40) + "T08:00:00+00:00",
     "jobExcerpt": "FICTIONAL.", "jobDescription": "<p>FICTIONAL.</p>"},
]}
ARBEITNOW = {"data": [
    {"slug": "qa-berlin", "title": "QA Engineer", "company_name": "Berlin GmbH (fictional)", "location": "Berlin",
     "remote": False, "url": "https://arbeitnow.example/qa-berlin", "created_at": 0, "description": "FICTIONAL."},
]}
ADZUNA = {"count": 2, "results": [
    {"id": "a1", "title": "<strong>QA</strong> Engineer", "company": {"display_name": "Northwind (fictional)"},
     "location": {"display_name": "Toronto, Ontario"}, "created": D(0) + "T09:00:00Z",
     "redirect_url": "https://adzuna.example/land/a1", "description": "FICTIONAL excerpt…"},
    {"id": "a2", "title": "Senior QA Engineer", "company": {"display_name": "Contoso (fictional)"},
     "location": {"display_name": "Remote"}, "created": D(1) + "T09:00:00Z",
     "redirect_url": "https://adzuna.example/land/a2", "description": "FICTIONAL excerpt…"},
]}


def make_fetch(calls: list, broken: set = frozenset()):
    def fetch(url):
        calls.append(url)
        for name in broken:
            if name in url:
                raise RuntimeError(f"{name} is down")
        if "remotive" in url:
            return REMOTIVE
        if "jobicy" in url:
            return JOBICY
        if "arbeitnow" in url:
            return ARBEITNOW
        if "adzuna" in url:
            return ADZUNA if "/search/1?" in url else {"results": []}
        raise AssertionError(url)
    return fetch


def test_filters_by_title_country_and_dates_and_merges_sources(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "fake-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "fake-key")
    calls: list[str] = []
    r = job_search.search("QA Engineer", "CA", TODAY - timedelta(days=7), TODAY, make_fetch(calls))
    got = {(j.title, j.company): j for j in r.results}
    assert ("Senior QA Engineer", "Contoso (fictional)") in got          # Remotive + Adzuna: kept once
    assert sum(1 for j in r.results if j.company == "Contoso (fictional)") == 1
    assert got[("Senior QA Engineer", "Contoso (fictional)")].source == "Remotive"   # full description beats excerpt
    assert ("QA Engineer", "Northwind (fictional)") in got                # Adzuna, Toronto
    assert got[("QA Engineer", "Globex (fictional)")].where == "Remote (worldwide)"
    assert ("Quality Assurance Engineer", "Hooli (fictional)") in got     # synonyms: Quality Assurance = QA
    assert ("QA Engineer", "Initech (fictional)") not in got              # Europe only
    assert ("QA Engineer", "Berlin GmbH (fictional)") not in got          # Germany
    assert ("QA Engineer", "Old Corp (fictional)") not in got             # outside the date range
    assert all(j.title != "Account Executive" for j in r.results)         # title doesn't match
    assert [j.posted for j in r.results] == sorted([j.posted for j in r.results], reverse=True)
    assert any("geo=canada" in c for c in calls) and any("/jobs/ca/search/1" in c for c in calls)
    assert {s.name for s in r.sources if s.ok} == {"Adzuna", "Remotive", "Jobicy", "Arbeitnow"}


def test_a_failing_source_doesnt_stop_the_others_and_adzuna_needs_a_key(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    r = job_search.search("QA Engineer", "CA", TODAY - timedelta(days=7), TODAY, make_fetch([], broken={"jobicy"}))
    status = {s.name: s for s in r.sources}
    assert not status["Jobicy"].ok and "down" in status["Jobicy"].note
    assert not status["Adzuna"].ok and "ADZUNA_APP_ID" in status["Adzuna"].note
    assert status["Remotive"].ok and r.results


@pytest.mark.parametrize("location, code, expected", [
    ("Toronto, ON", "CA", "Toronto, ON"),
    ("Remote (USA or Canada)", "CA", "Remote (USA or Canada)"),
    ("North America", "CA", None),                       # a region but not said to be remote
    ("Remote (North America)", "US", "Remote (North America)"),
    ("New York, NY", "US", "New York, NY"),
    ("New York, NY", "CA", None),
    ("Remote - Europe", "DE", "Remote - Europe"),
    ("Bengaluru, Karnataka", "IN", "Bengaluru, Karnataka"),
    ("Worldwide", "GB", "Remote (worldwide)"),
    ("London, UK", "CA", None),
])
def test_where_a_job_counts(location, code, expected):
    assert where_for(location, COUNTRIES[code]) == expected


@pytest.fixture
def search_client(db_path, fake_llm):
    with TestClient(create_app(db_path, llm=fake_llm, fetch=make_fetch([]))) as c:
        yield c


def test_search_api_and_apply_saves_the_job_and_starts_the_application(search_client, monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    countries = search_client.get("/search/countries").json()
    assert any(c["code"] == "CA" for c in countries["countries"]) and countries["adzuna_configured"] is False
    body = {"title": "QA Engineer", "country": "CA", "date_from": D(7), "date_to": D(0)}
    r = search_client.post("/search", json=body)
    assert r.status_code == 200, r.text
    first = r.json()["results"][0]
    applied = search_client.post("/search/apply", json={"job": first}).json()
    assert applied["new_job"] and applied["new_application"] and applied["apply_url"] == first["url"]
    job = search_client.get(f"/jobs/{applied['job_id']}").json()
    assert (job["title"], job["company"]) == (first["title"], first["company"])
    again = search_client.post("/search/apply", json={"job": first}).json()          # no duplicates
    assert (again["job_id"], again["application_id"]) == (applied["job_id"], applied["application_id"])
    assert again["new_job"] is False and again["new_application"] is False
    marked = search_client.post("/search", json=body).json()["results"]
    assert next(j for j in marked if j["url"] == first["url"])["already_added_job_id"] == applied["job_id"]
    status = search_client.get(f"/applications/{applied['application_id']}").json()["status"]
    assert status == "preparing"                                                       # never marked as applied


def test_bad_filters(search_client):
    assert search_client.post("/search", json={"title": "QA", "country": "XX", "date_from": D(7),
                                               "date_to": D(0)}).status_code == 422
    assert search_client.post("/search", json={"title": "QA", "country": "CA", "date_from": D(0),
                                               "date_to": D(7)}).status_code == 422
