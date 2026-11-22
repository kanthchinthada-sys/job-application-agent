"""Tailored resume: selection, verified rewording, summary fallback, printable page."""

import json
from datetime import datetime

import pytest

from app.schemas import JobOut, Profile
from app.tailoring import check_rewording, order_skills, select_bullets


@pytest.fixture
def job(job_data):
    return JobOut(id=1, created_at=datetime(2026, 1, 1), **job_data)


@pytest.fixture
def app_id(client, profile_data, make_job):
    client.put("/profile", json=profile_data)
    return client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]


GOOD_SUMMARY = {"sentences": []}   # no AI sentences -> summary sentences are shortened without AI
GOOD_CLAUSES = {"sentences": [
    {"id": "T1", "text": "Built pytest suites for a payments API used by three internal teams at Example Corp (fictional)."},
    {"id": "T2", "text": "Reproduced production issues with customer support using Docker containers."},
]}


def make_resume(client, app_id):
    return client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"})


# ---- pure functions ----

def test_most_relevant_bullet_ranked_first(profile_data, job):
    selection = select_bullets(Profile(**profile_data), job)
    first_role_bullets = selection[0][1]
    # The Docker bullet matches the job's skills (Docker); the pytest bullet matches less.
    assert "Docker" in first_role_bullets[0]


def test_skills_matching_the_job_come_first(profile_data, job):
    skills = order_skills(Profile(**profile_data), job)
    assert set(skills[:3]) == {"Python", "pytest", "REST APIs"}  # exact matches with the job first
    assert set(skills) == set(profile_data["skills"])


@pytest.mark.parametrize("new, reason_contains", [
    ("Built a pytest suite for a payments API used by 5 internal teams.", "added numbers"),
    ("Built a pytest and Kubernetes suite for a payments API used by three internal teams.", "Kubernetes"),
    ("Built a pytest suite for a payments API used by three internal teams, leveraging robust tooling.", "AI-sounding"),
    ("Built a pytest suite " + "very " * 20 + "fast.", "grew too much"),
])
def test_bad_rewordings_are_rejected(job, new, reason_contains):
    original = "Built a pytest suite for a payments API used by three internal teams."
    assert reason_contains in check_rewording(original, new, job)


def test_honest_rewording_is_accepted(job):
    original = "Built a pytest suite for a payments API used by three internal teams."
    assert check_rewording(original, "Built pytest API test automation for a payments API used by three internal teams.", job) is None


# ---- endpoint ----

def test_resume_created_with_verified_rewording(client, fake_llm, app_id, profile_data):
    original = profile_data["experience"][0]["highlights"][0]
    fake_llm.queue = [
        {"bullets": [{"id": "B1.2", "text": "Built pytest API test automation for a payments API used by three internal teams."},
                     {"id": "B1.1", "text": "Reproduced production issues on AWS with 99% uptime."}]},  # rejected
        GOOD_CLAUSES,
    ]
    response = make_resume(client, app_id)
    assert response.status_code == 201
    doc = response.json()
    resume = json.loads(doc["content"])
    bullets = resume["experience"][0]["bullets"]
    assert any(b["changed"] and "API test automation" in b["text"] and b["original"] == original for b in bullets)
    rejected = [b for b in bullets if b["rejected_reason"]]
    assert rejected and not rejected[0]["changed"] and "AWS" not in rejected[0]["text"]
    assert resume["summary_source"] == "composed"
    assert resume["summary"].startswith("FICTIONAL profile for development only.")      # your own opening sentence
    assert len(resume["summary"].split(". ")) >= 2                                      # plus job-theme sentence(s)
    assert doc["review"]["tailoring"]["bullets_reworded"] == 1
    assert doc["review"]["tailoring"]["rewordings_rejected"] == 1
    assert "address" not in resume["contact"]


def test_missing_rewordings_keep_originals(client, fake_llm, app_id):
    fake_llm.queue = [{"bullets": []}, GOOD_SUMMARY]
    resume = json.loads(make_resume(client, app_id).json()["content"])
    assert all(not b["changed"] and b["text"] == b["original"] for r in resume["experience"] for b in r["bullets"])


def test_printable_page(client, fake_llm, app_id, profile_data):
    fake_llm.queue = [{"bullets": []}, GOOD_SUMMARY]
    doc = make_resume(client, app_id).json()
    page = client.get(f"/documents/{doc['id']}/print")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert profile_data["full_name"] in page.text and "Save as PDF" in page.text
    assert "Mar 2021 - Present" in page.text


def test_a_letter_prints_as_a_letter_downloads_as_text_and_missing_is_404(client, fake_llm, app_id):
    fake_llm.answer = {"text": "Body.", "fact_ids_used": ["F1"]}
    letter = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    page = client.get(f"/documents/{letter['id']}/print")
    assert page.status_code == 200 and "<p>" in page.text
    file = client.get(f"/documents/{letter['id']}/download")
    assert file.status_code == 200 and "attachment" in file.headers["content-disposition"]
    assert file.text == letter["content"]
    assert client.get("/documents/999/print").status_code == 404
    assert client.get("/documents/999/download").status_code == 404


def test_resume_versions_and_validated_edits(client, fake_llm, app_id):
    fake_llm.queue = [{"bullets": []}, GOOD_SUMMARY, {"bullets": []}, GOOD_SUMMARY]
    first = make_resume(client, app_id).json()
    second = make_resume(client, app_id).json()
    assert (first["version"], second["version"]) == (1, 2)
    assert client.post(f"/documents/{second['id']}/versions", json={"content": "not json"}).status_code == 409
    edited = json.loads(second["content"]); edited["summary"] = "My own summary."
    ok = client.post(f"/documents/{second['id']}/versions", json={"content": json.dumps(edited)})
    assert ok.status_code == 201 and ok.json()["version"] == 3


def test_resume_rejects_question(client, app_id):
    assert client.post(f"/applications/{app_id}/drafts", json={"kind": "resume", "question": "x"}).status_code == 422


def test_reorder_summary_keeps_sentences_and_first_line(job):
    from app.tailoring import reorder_summary
    summary = "QA engineer for 5 years. I like hiking. I build Python REST APIs tests with Docker."
    out = reorder_summary(summary, job)
    assert out == "QA engineer for 5 years. I build Python REST APIs tests with Docker. I like hiking."


def test_projects_appear_on_printable_resume(client, fake_llm, profile_data, make_job):
    client.put("/profile", json=profile_data)
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    fake_llm.queue = [{"bullets": []}, GOOD_SUMMARY]
    doc = make_resume(client, app_id).json()
    assert json.loads(doc["content"])["projects"][0]["name"] == "Job Application Agent"
    page = client.get(f"/documents/{doc['id']}/print").text
    assert "<h2>Projects</h2>" in page and "Job Application Agent" in page


def test_resume_follows_users_format(client, fake_llm, profile_data, make_job):
    profile_data["experience"][0]["location"] = "Exampleville, CA"
    profile_data["experience"][0]["recognition"] = "FICTIONAL Team Award, 2024"
    profile_data["skill_groups"] = [
        {"name": "QA leadership", "skills": ["Test planning", "Defect triage"]},
        {"name": "CI/CD and development tools", "skills": ["Jenkins", "Git"]},
    ]
    profile_data["experience"][0]["highlights"].append("Ran nightly regression suites in Jenkins.")
    profile_data["projects"][0]["link"] = "https://github.com/example/agent"
    client.put("/profile", json=profile_data)
    job = make_job(required_skills=["Python", "CI/CD pipelines"])
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.queue = [{"bullets": []}, GOOD_SUMMARY]
    doc = make_resume(client, app_id).json()
    resume = json.loads(doc["content"])

    groups = {g["name"]: g["skills"] for g in resume["skill_groups"]}
    assert "CI/CD pipelines" in groups["CI/CD and development tools"]     # ATS keyword placed with its evidence
    page = client.get(f"/documents/{doc['id']}/print").text
    assert "<b>QA leadership:</b> Test planning, Defect triage" in page
    assert "Example Corp (fictional) | Exampleville, CA" in page
    assert "<b>Recognition:</b> FICTIONAL Team Award, 2024" in page
    assert "github.com/example/agent" in page
    assert profile_data["headline"] not in page                            # no headline under the name
    assert "Times New Roman" in page



def test_summary_sentence_with_added_claim_falls_back_to_your_wording(client, fake_llm, app_id):
    bad = {"sentences": [{"id": "T1", "text": "Deployed services on AWS with customer support at Example Corp (fictional)."},
                         {"id": "T2", "text": "Deployed services on AWS with customer support at Example Corp (fictional)."}]}
    fake_llm.queue = [{"bullets": [], **bad}]                # one AI call writes bullets AND summary sentences
    doc = make_resume(client, app_id).json()
    summary = json.loads(doc["content"])["summary"]
    assert "AWS" not in summary
    assert any("written without AI" in w and "AWS" in w for w in doc["review"]["notes"])     # a note, not an alarm
    assert not any("written without AI" in w for w in doc["review"]["warnings"])


def test_one_unbacked_summary_sentence_is_left_out_not_the_whole_summary(client, fake_llm, app_id, monkeypatch):
    from app import tailoring
    own = "QA engineer working on payments (FICTIONAL)."
    good = "Built a pytest suite for a payments API used by three internal teams."
    bad = "Cut cloud cost optimization spend by 47% across 12 data centers."
    monkeypatch.setattr(tailoring, "compose_summary", lambda *a, **k: (f"{own} {good} {bad}", []))
    fake_llm.queue = [{"bullets": []}]
    doc = make_resume(client, app_id).json()
    resume = json.loads(doc["content"])
    assert resume["summary_source"] == "composed"
    assert good in resume["summary"] and "cost optimization" not in resume["summary"]
    assert any("left out" in n for n in doc["review"]["notes"])


def test_summary_failing_the_checks_falls_back_to_own_summary(client, fake_llm, app_id, profile_data, monkeypatch):
    from app import tailoring
    monkeypatch.setattr(tailoring, "compose_summary", lambda *a, **k: ("Led 12 engineers deploying on AWS.", []))
    fake_llm.queue = [{"bullets": []}]
    doc = make_resume(client, app_id).json()
    resume = json.loads(doc["content"])
    assert resume["summary_source"] == "profile_reordered" and "AWS" not in resume["summary"]
    assert doc["review"]["tailoring"]["ai_summary_rejected"]["text"] == "Led 12 engineers deploying on AWS."


def test_summary_adds_ai_sentence_when_job_mentions_ai(client, fake_llm, profile_data, make_job):
    profile_data["summary"] = "QA engineer with 5 years in payments. I use GitHub Copilot to draft tests."
    client.put("/profile", json=profile_data)
    job = make_job(description="FICTIONAL. Build test automation and use generative AI tools such as Copilot daily.")
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.queue = [{"bullets": []}, {"sentences": []}]
    summary = json.loads(make_resume(client, app_id).json()["content"])["summary"]
    assert summary.startswith("QA engineer with 5 years in payments.") and summary.endswith("I use GitHub Copilot to draft tests.")


def test_added_keywords_go_to_sensible_skill_groups(client, fake_llm, profile_data, make_job):
    profile_data["skill_groups"] = [
        {"name": "QA leadership", "skills": ["Test planning", "defect triage"]},
        {"name": "Automation", "skills": ["regression testing", "Selenium"]},
        {"name": "CI/CD and development tools", "skills": ["Jenkins"]},
        {"name": "AI-assisted development and testing", "skills": ["GitHub Copilot", "code review"]},
    ]
    profile_data["experience"][0]["highlights"] += [
        "Led defect triage and business acceptance testing for releases.",
        "Ran nightly regression suites in Jenkins.", "Used GitHub Copilot to draft API tests."]
    client.put("/profile", json=profile_data)
    job = make_job(required_skills=["UAT testing", "CI/CD pipelines", "Generative AI"])
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.queue = [{"bullets": []}, {"sentences": []}]
    groups = {g["name"]: g["skills"] for g in json.loads(make_resume(client, app_id).json()["content"])["skill_groups"]}
    assert "UAT testing" in groups["QA leadership"]
    assert "CI/CD pipelines" in groups["CI/CD and development tools"]
    assert "Generative AI" in groups["AI-assisted development and testing"]



def test_summary_themes_follow_the_job(profile_data):
    from app.tailoring import job_themes
    from datetime import datetime
    lead = JobOut(id=1, title="QA Team Lead", company="X", description="FICTIONAL. Lead a team, own the test strategy "
                  "and UAT, report readiness to stakeholders. Some automation.", required_skills=["Leadership"],
                  preferred_skills=[], created_at=datetime(2026, 1, 1))
    sdet = JobOut(id=2, title="SDET", company="X", description="FICTIONAL. Build API automation frameworks with "
                  "Playwright, run them in Jenkins pipelines on Kubernetes.", required_skills=["Playwright"],
                  preferred_skills=[], created_at=datetime(2026, 1, 1))
    assert job_themes(lead)[0][0] == "leadership"
    assert job_themes(sdet)[0][0] == "automation" and "leadership" not in [t for t, _ in job_themes(sdet)]


def test_fallback_clause_cuts_cleanly():
    from app.tailoring import _fallback_clause
    out = _fallback_clause(["Brought test results, unresolved defects, and outstanding checks into release "
                            "discussions, giving stakeholders a clear view; then more."])
    assert out == "Brought test results, unresolved defects, and outstanding checks into release discussions, giving stakeholders a clear view."


@pytest.mark.parametrize("text, expected", [
    ("At Northwind Bank, led defect triage for AML releases.", "Led defect triage for AML releases."),
    ("Built Playwright suites at Contoso Health and Selenium tests at Fabrikam Freight.", "Built Playwright suites and Selenium tests."),
    ("Led ETL testing for Northwind Bank's anti-money laundering platform.", "Led ETL testing for Northwind Bank's anti-money laundering platform."),
])
def test_scrub_employer_attributions_keeps_meaningful_mentions(text, expected):
    from app.tailoring import scrub_employers
    assert scrub_employers(text, _four_employers()) == expected


def test_only_one_sentence_may_name_an_employer():
    from app.tailoring import limit_employer_mentions
    out = limit_employer_mentions(["Led testing for Northwind Bank's AML platform.", "Built Contoso Health's Playwright suites."],
                                  _four_employers())
    assert out == ["Led testing for Northwind Bank's AML platform.", "Built the Playwright suites."]


def _four_employers():
    return Profile(full_name="X (FICTIONAL)", experience=[
        {"company": c, "title": "QA", "start_date": "2020-01"}
        for c in ["Contoso Health", "Northwind Bank", "Fabrikam Freight", "Tailspin Labs"]])


def test_rewording_may_use_job_terms_the_original_evidences(job):
    original = "Ran nightly regression suites in Jenkins."
    job.required_skills.append("CI/CD pipelines")
    assert check_rewording(original, "Ran nightly regression suites in Jenkins CI/CD pipelines.", job) is None


def test_summary_has_no_per_employer_attributions(client, fake_llm, app_id, profile_data):
    named = {"sentences": [{"id": "T1", "text": "At Example Corp (fictional), reproduced production issues with customer support using Docker containers."},
                           {"id": "T2", "text": "Wrote regression tests at Sample Systems (fictional)."}]}
    fake_llm.queue = [{"bullets": []}, named]
    summary = json.loads(make_resume(client, app_id).json()["content"])["summary"]
    assert "At Example Corp" not in summary and "at Sample Systems" not in summary


def test_latest_resume_link_follows_new_versions(client, fake_llm, app_id):
    assert client.get(f"/applications/{app_id}/resume").status_code == 404
    fake_llm.queue = [{"bullets": []}, {"sentences": []}, {"bullets": []}, {"sentences": []}]
    make_resume(client, app_id)
    second = make_resume(client, app_id).json()
    page = client.get(f"/applications/{app_id}/resume")
    assert page.status_code == 200
    assert page.text == client.get(f"/documents/{second['id']}/print").text
