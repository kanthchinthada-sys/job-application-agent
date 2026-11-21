"""The added job sources, each fed a response shaped like its documented public API. FICTIONAL data, no network."""

from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote

from app import job_search
from app.job_search import COUNTRIES

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()                       # noqa: E731
CA = COUNTRIES["CA"]

MUSE = {"page": 0, "page_count": 1, "results": [
    {"id": 1, "name": "QA Automation Engineer", "publication_date": D(1) + "T10:00:00Z",
     "locations": [{"name": "Toronto, Canada"}, {"name": "New York, NY"}], "contents": "<p>FICTIONAL. Playwright.</p>",
     "company": {"id": 9, "name": "Contoso (fictional)"},
     "refs": {"landing_page": "https://www.themuse.com/jobs/contoso/qa-automation-engineer"}},
    {"id": 2, "name": "QA Engineer", "publication_date": D(1) + "T10:00:00Z",
     "locations": [{"name": "Austin, TX"}], "contents": "FICTIONAL.", "company": {"name": "Texan (fictional)"},
     "refs": {"landing_page": "https://www.themuse.com/jobs/texan/qa"}},
]}
HIMALAYAS = {"totalCount": 2, "jobs": [
    {"title": "Senior QA Engineer", "companyName": "Globex (fictional)", "locationRestrictions": ["Canada", "United States"],
     "pubDate": int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp()),          # seconds
     "applicationLink": "https://globex.example/apply/1", "guid": "https://himalayas.app/companies/globex/jobs/qa",
     "excerpt": "FICTIONAL excerpt.", "description": "<p>FICTIONAL full.</p>"},
    {"title": "QA Engineer", "companyName": "Euro (fictional)", "locationRestrictions": ["Germany"],
     "pubDate": 0, "applicationLink": "https://euro.example/apply", "guid": "x"},
]}
REMOTEOK = [
    {"legal": "API terms: link back to Remote OK and mention it as the source."},
    {"id": "77", "position": "QA Engineer", "company": "Initech (fictional)", "location": "Worldwide",
     "date": D(3) + "T08:00:00+00:00", "url": "https://remoteok.com/remote-jobs/77", "apply_url": "https://remoteok.com/l/77",
     "description": "FICTIONAL."},
]
JSEARCH = {"status": "OK", "data": [
    {"job_title": "QA Lead (Automation)", "employer_name": "Northwind (fictional)", "job_publisher": "LinkedIn",
     "job_city": "Toronto", "job_state": "ON", "job_country": "CA", "job_location": "Toronto, ON",
     "job_is_remote": False, "job_posted_at_datetime_utc": D(0) + "T00:00:00.000Z",
     "job_apply_link": "https://northwind.example/careers/1", "job_description": "FICTIONAL. Selenium."},
]}
JOOBLE = {"totalCount": 1, "jobs": [
    {"title": "<b>QA</b> Engineer", "location": "Mississauga, ON", "snippet": "FICTIONAL snippet…", "company": "Hooli (fictional)",
     "link": "https://jooble.org/desc/1", "updated": D(1) + "T00:00:00.0000000", "type": "Full-time"},
]}


def test_muse_keeps_the_city_in_your_country_and_credits_the_source():
    seen = []
    jobs = job_search.src_muse(lambda u: (seen.append(u), MUSE)[1], "QA Engineer", CA)
    j = jobs[0]
    assert j.location == "Toronto, Canada" and j.credit_url.startswith("https://www.themuse.com/")
    assert "location=Toronto%2C+Canada" in seen[0] and "Flexible+%2F+Remote" in seen[0]


def test_himalayas_uses_the_apply_link_unchanged_and_links_back():
    jobs = job_search.src_himalayas(lambda u: HIMALAYAS, "QA Engineer", CA)
    j = jobs[0]
    assert j.url == "https://globex.example/apply/1"
    assert j.credit_url.startswith("https://himalayas.app/") and j.location == "Remote (Canada, United States)"
    assert job_search.where_for(jobs[1].location, CA, True) is None          # Germany only


def test_remoteok_skips_the_legal_notice_and_links_to_its_page():
    jobs = job_search.src_remoteok(lambda u: REMOTEOK, "Senior QA Engineer", CA)
    assert [j.company for j in jobs] == ["Initech (fictional)"]
    assert jobs[0].url == jobs[0].credit_url == "https://remoteok.com/remote-jobs/77"


def test_keyed_sources_send_the_key_and_read_the_answer(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "fake-rapidapi-key")
    monkeypatch.setenv("JOOBLE_API_KEY", "fake-jooble-key")
    calls = []

    def request(method, url, headers, body):
        calls.append((method, url, headers, body))
        return JSEARCH if "jsearch" in url else JOOBLE

    js = job_search.src_jsearch(request, "QA Lead", CA, 7)
    assert js[0].source == "LinkedIn (via JSearch)" and js[0].location == "Toronto, ON, Canada"
    assert calls[0][2]["X-RapidAPI-Key"] == "fake-rapidapi-key" and "date_posted=week" in calls[0][1]
    jo = job_search.src_jooble(request, "QA Engineer", CA)
    assert jo[0].title == "QA Engineer" and jo[0].description_is_excerpt
    assert calls[1][0] == "POST" and calls[1][1].endswith("/fake-jooble-key") and calls[1][3]["location"] == "Canada"


def test_search_uses_every_source_and_offers_links_elsewhere(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)

    def fetch(url):
        if "themuse" in url:
            return MUSE
        if "himalayas" in url:
            return HIMALAYAS
        if "remoteok" in url:
            return REMOTEOK
        return {"jobs": [], "data": [], "results": []}

    r = job_search.search("QA Engineer", "CA", TODAY - timedelta(days=7), TODAY, fetch,
                          request=lambda m, u, h, b: JSEARCH if "jsearch" in u else JOOBLE)
    companies = {j.company for j in r.results}
    assert {"Contoso (fictional)", "Initech (fictional)", "Hooli (fictional)"} <= companies
    assert "Texan (fictional)" not in companies                                # Austin isn't in Canada
    ok = {s.name for s in r.sources if s.ok}
    assert {"The Muse", "Himalayas", "RemoteOK", "JSearch", "Jooble"} <= ok
    names = {link.name: link.url for link in r.elsewhere}
    assert {"hiring.cafe", "LinkedIn", "Indeed", "Glassdoor", "Job Bank (Government of Canada)"} <= set(names)
    assert "QA Engineer" in unquote(names["hiring.cafe"]) and names["Indeed"].startswith("https://ca.indeed.com/")


def test_keyed_sources_are_off_without_a_key(monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    r = job_search.search("QA Engineer", "CA", TODAY - timedelta(days=7), TODAY, lambda u: {"jobs": []},
                          request=lambda *a: (_ for _ in ()).throw(AssertionError("no call without a key")))
    off = {s.name: s.note for s in r.sources if not s.ok}
    assert "JSEARCH_API_KEY" in off["JSearch"] and "JOOBLE_API_KEY" in off["Jooble"]


def test_an_openwebninja_key_uses_their_address(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "ak_fake_key")
    calls = []
    nested = {"status": "OK", "data": {"jobs": JSEARCH["data"]}}
    jobs = job_search.src_jsearch(lambda m, u, h, b: (calls.append((u, h)), nested)[1], "QA Lead", CA, 7)
    assert calls[0][0].startswith("https://api.openwebninja.com/jsearch/search-v2?") and "num_pages=1" in calls[0][0]
    assert calls[0][1] == {"X-API-Key": "ak_fake_key"}
    assert jobs[0].company == "Northwind (fictional)"
