"""Apify in Find jobs (fixtures only: no network, nothing paid). Every company and job here is FICTIONAL."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import apify_search
from app.api import create_app
from tests.conftest import FakeLLM
from tests.test_job_search import SchemaLLM, make_fetch


def D(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


ITEMS = [
    {"title": "QA Engineer", "company": {"name": "Northwind Fictional"}, "source": "workday",
     "listing_url": "https://northwind.wd3.example/careers/qa-engineer-1",
     "apply_url": "https://northwind.wd3.example/careers/qa-engineer-1/apply",
     "locations": [{"city": "Toronto", "region": "Ontario", "country": "Canada"}],
     "date_posted": f"{D(2)}T10:00:00Z", "workplace_type": "hybrid",
     "description": "## About the role\nBuild **API** test automation in Java for payments (fictional)."},
    # the same job again (another ATS page): de-duplicated
    {"title": "QA Engineer", "company": "Northwind Fictional", "source": "greenhouse",
     "listing_url": "https://boards.example/northwind/2", "locations": ["Toronto, ON, Canada"],
     "date_posted": f"{D(2)}T10:00:00Z", "description": "Same job (fictional)."},
    # outside the country: dropped
    {"title": "QA Engineer", "company": "Contoso Fictional", "source": "lever",
     "listing_url": "https://jobs.example/contoso/3", "locations": ["Berlin, Germany"],
     "date_posted": f"{D(1)}T10:00:00Z", "description": "(fictional)"},
    # outside the dates: dropped
    {"title": "QA Engineer", "company": "Tailspin Fictional", "source": "icims",
     "listing_url": "https://careers.example/tailspin/4", "locations": ["Vancouver, BC, Canada"],
     "date_posted": f"{D(60)}T10:00:00Z", "description": "(fictional)"},
    # no company and no description given: those fields stay blank
    {"title": "Senior QA Engineer", "source": "ashby", "listing_url": "https://jobs.example/fabrikam/5",
     "locations": ["Remote - Canada"], "date_posted": f"{D(0)}T08:00:00Z"},
]


class FakeApify:
    """Apify's API for the tests: records every call; the run's status is set by the test."""

    def __init__(self, items=None, status="RUNNING", fail: str | None = None):
        self.calls: list[tuple[str, str, object]] = []
        self.items = ITEMS if items is None else items
        self.status = status
        self.fail = fail

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if self.fail:
            raise apify_search.ApifyError(self.fail)
        if method == "POST" and "/runs?" in url:
            return {"id": f"run{len(self.starts())}", "defaultDatasetId": "ds1", "status": "READY"}
        if method == "GET" and "/actor-runs/" in url:
            return {"id": "run1", "status": self.status, "defaultDatasetId": "ds1",
                    "usageTotalUsd": getattr(self, "cost", 0.02)}
        if method == "POST" and url.endswith("/abort"):
            self.status = "ABORTED"
            return {"status": "ABORTED"}
        if "/datasets/" in url:
            return self.items
        raise AssertionError(url)

    def starts(self):
        return [c for c in self.calls if c[0] == "POST" and "/runs?" in c[1]]


BODY = {"title": "QA Engineer", "country": "CA", "date_from": D(7), "date_to": D(0)}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "fictional-test-token")
    monkeypatch.delenv("APIFY_ACTOR_ID", raising=False)
    monkeypatch.setenv("APIFY_MAX_RESULTS", "25")
    monkeypatch.setenv("APIFY_TIMEOUT_SECS", "60")
    monkeypatch.setenv("APIFY_MAX_COST_USD", "0.20")


def _client(db_path, fake, llm=None):
    app = create_app(db_path, llm=llm or FakeLLM(), fetch=make_fetch([]))
    app.state.apify_request = fake
    return TestClient(app)


def test_without_a_token_apify_is_off_and_nothing_is_sent(db_path, monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    fake = FakeApify()
    with _client(db_path, fake) as c:
        s = c.get("/search/apify").json()
        assert s["settings"]["configured"] is False and s["latest"] is None
        assert "token" not in json.dumps(s["settings"]).lower().replace("configured", "")
        r = c.post("/search/apify", json=BODY)
        assert r.status_code == 503 and "APIFY_API_TOKEN" in r.json()["detail"]
    assert fake.calls == []


def test_a_run_sends_only_the_filters_and_its_results_are_filtered_and_deduplicated(db_path, configured):
    fake = FakeApify()
    with _client(db_path, fake) as c:
        started = c.post("/search/apify", json=BODY).json()
        assert started["active"] and started["results"] == []
        method, url, sent = fake.starts()[0]
        assert "/acts/jobo.world~ats-jobs-api/runs?" in url
        assert "timeout=60" in url and "maxItems=25" in url and "maxTotalChargeUsd=0.2" in url   # the limits
        assert set(sent) == {"queries", "locations", "search_description", "posted_after", "posted_before",
                             "page_size", "description_format"}
        assert sent["queries"] == ["QA Engineer"] and sent["locations"] == ["Canada"] and sent["page_size"] == 25
        fake.status = "SUCCEEDED"
        done = c.get(f"/search/apify/{started['id']}").json()
    assert done["status"] == "SUCCEEDED" and not done["active"] and done["cost_usd"] == 0.02
    by_company = {j["company"]: j for j in done["results"]}
    assert set(by_company) == {"Northwind Fictional", ""}                   # Berlin and the 60-day-old job dropped
    nw = by_company["Northwind Fictional"]
    assert nw["url"] == "https://northwind.wd3.example/careers/qa-engineer-1"          # the employer's own page
    assert nw["credit_url"].endswith("/apply") and nw["source"] == "Workday career site (via Apify)"
    assert "**" not in nw["description"] and "##" not in nw["description"]
    blank = by_company[""]
    assert blank["description"] == "" and blank["company"] == ""            # unavailable fields stay blank
    assert done["found"] == 5 and done["kept"] == 2


def test_a_double_click_or_a_retry_never_starts_a_second_paid_run(db_path, configured):
    fake = FakeApify()
    with _client(db_path, fake) as c:
        first = c.post("/search/apify", json=BODY).json()
        second = c.post("/search/apify", json=BODY).json()
        other = c.post("/search/apify", json={**BODY, "title": "SDET"}).json()   # another search while one runs
        assert first["id"] == second["id"] == other["id"] and second["reused"] == "active"
        assert len(fake.starts()) == 1
        fake.status = "SUCCEEDED"
        c.get(f"/search/apify/{first['id']}")
        again = c.post("/search/apify", json=BODY).json()                        # same filters, just finished
        assert again["id"] == first["id"] and again["reused"] == "recent" and len(fake.starts()) == 1


def test_a_run_past_its_time_limit_is_stopped_and_says_so(db_path, configured):
    fake = FakeApify()
    with _client(db_path, fake) as c:
        run = c.post("/search/apify", json=BODY).json()
        import sqlite3          # the stored start time is moved back past the limit (60 s + 60 s grace)
        db = sqlite3.connect(db_path)
        db.execute("UPDATE apify_runs SET started_at = ?", ("2000-01-01T00:00:00+00:00",))
        db.commit()
        db.close()
        done = c.get(f"/search/apify/{run['id']}").json()
        assert done["status"] == "TIMED-OUT" and "longer than 60 seconds" in done["message"]
        assert any(m == "POST" and u.endswith("/abort") for m, u, _ in fake.calls)
        fake.status = "RUNNING"                                                  # Apify's next run
        assert c.post("/search/apify", json={**BODY, "title": "SDET"}).json()["active"]      # a new one may start


def test_apify_errors_are_clear_and_do_not_block_the_next_search(db_path, configured):
    fake = FakeApify(fail="Apify refused the token (HTTP 401): check APIFY_API_TOKEN in .env.")
    with _client(db_path, fake) as c:
        r = c.post("/search/apify", json=BODY)
        assert r.status_code == 502 and "refused the token" in r.json()["detail"]
        assert c.get("/search/apify").json()["latest"]["status"] == "ERROR"
        fake.fail = None
        assert c.post("/search/apify", json=BODY).json()["active"]             # the failed one doesn't block


def test_an_apify_result_saves_and_prepares_like_any_other(db_path, configured, profile_data, monkeypatch):
    from app import drafting
    monkeypatch.setattr(drafting, "MIN_LETTER_WORDS", 0)
    fake = FakeApify(status="SUCCEEDED")
    with _client(db_path, fake, llm=SchemaLLM()) as c:
        c.put("/profile", json=profile_data)
        run = c.post("/search/apify", json=BODY).json()
        found = next(j for j in run["results"] if j["company"] == "Northwind Fictional")
        applied = c.post("/search/apply", json={"job": found}).json()
        assert applied["new_job"] and applied["apply_url"] == found["url"]
        job = c.get(f"/jobs/{applied['job_id']}").json()
        assert job["source_url"] == found["url"] and job["company"] == "Northwind Fictional"
        c.app.state.preparer.wait()
        status = c.get(f"/applications/{applied['application_id']}/prepare").json()
        assert status["state"] == "done", status
        kinds = {d["kind"] for d in c.get(f"/applications/{applied['application_id']}/documents").json()}
        assert kinds == {"resume", "cover_letter"}
        marked = c.get(f"/search/apify/{run['id']}").json()["results"]
        assert next(j for j in marked if j["company"] == "Northwind Fictional")["already_added_job_id"] == applied["job_id"]
        again = c.post("/search/apply", json={"job": found}).json()            # saving it twice: one job
        assert again["job_id"] == applied["job_id"] and again["new_job"] is False


def test_each_account_sees_only_its_own_apify_searches(tmp_path, configured):
    fake = FakeApify()
    app = create_app(tmp_path / "job_agent.db", llm=FakeLLM(), fetch=make_fetch([]), accounts=True)
    app.state.apify_request = fake
    with TestClient(app) as alex, TestClient(app) as sam:
        assert alex.post("/auth/register", json={"username": "alex.fictional", "password": "correct horse 1"}).status_code == 200
        assert sam.post("/auth/register", json={"username": "sam.fictional", "password": "correct horse 2"}).status_code == 200
        run = alex.post("/search/apify", json=BODY).json()
        assert sam.get(f"/search/apify/{run['id']}").status_code == 404
        assert sam.get("/search/apify").json()["latest"] is None
        # one account's active run never blocks another account's search
        assert sam.post("/search/apify", json=BODY).json()["id"] == 1 and len(fake.starts()) == 2


def test_the_cost_shown_is_read_again_after_the_run_finishes(db_path, configured):
    fake = FakeApify(status="SUCCEEDED")
    with _client(db_path, fake) as c:
        run = c.post("/search/apify", json=BODY).json()
        assert run["cost_usd"] == 0.02
        fake.cost = 0.10005                       # Apify adds the per-result charges a moment later
        assert c.get(f"/search/apify/{run['id']}").json()["cost_usd"] == 0.10005


def test_a_location_never_repeats_a_part():
    assert apify_search._locations({"locations": ["Toronto, ON, ON"]}) == "Toronto, ON"
