"""ATS keyword analysis: literal matching, evidence-based additions, honest gaps."""

import json

from app.ats import analyze, find_evidence, keyword_in

PROFILE_TEXT = (
    "Led defect triage and maintained test evidence for AML releases, including business acceptance testing.\n"
    "Run automated checks through Jenkins and Maven.\nUse GitHub Copilot to draft API tests."
)


def test_literal_matching_with_word_boundaries():
    assert keyword_in("Built Playwright suites", "playwright")
    assert keyword_in("Tested .NET services", "NET")
    assert not keyword_in("network monitoring", "NET")
    assert not keyword_in("Postgres databases", "PostgreSQL")   # ATS matches literally...
    assert find_evidence("Tuned Postgres databases.", "PostgreSQL")  # ...but it is evidence we can use


def test_evidence_via_equivalent_wording():
    assert "business acceptance testing" in find_evidence(PROFILE_TEXT, "UAT testing")
    assert "Jenkins" in find_evidence(PROFILE_TEXT, "CI/CD pipelines")
    assert find_evidence(PROFILE_TEXT, "C#") is None


def test_score_and_buckets():
    report = analyze("Defect triage. Python.", PROFILE_TEXT, ["Defect triage", "UAT testing", "C#"], ["Python"])
    assert [k.keyword for k in report.matched] == ["Defect triage", "Python"]
    assert [k.keyword for k in report.missing_but_evidenced] == ["UAT testing"]
    assert [k.keyword for k in report.gaps] == ["C#"]
    assert report.score == round(100 * 3 / 7, 1)          # (2 + 1) / (2 + 2 + 2 + 1)
    assert report.max_honest_score == round(100 * 5 / 7, 1)


def test_excluded_keywords_are_ignored():
    report = analyze("", "", ["QA Team Lead", "Python"], [], exclude={"qa team lead"})
    assert [k.keyword for k in report.gaps] == ["Python"]


def test_tailored_resume_adds_only_evidenced_keywords(client, fake_llm, profile_data, make_job):
    profile_data["experience"][0]["highlights"].append("Ran nightly regression suites in Jenkins.")
    client.put("/profile", json=profile_data)
    job = make_job(required_skills=["Python", "CI/CD pipelines", "C#"], preferred_skills=[])
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.queue = [{"bullets": []}, {"text": "QA engineer who builds pytest suites.", "fact_ids_used": ["F4"]}]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    resume, review = json.loads(doc["content"]), doc["review"]
    assert resume["skills"][0] == "CI/CD pipelines"
    assert "C#" not in resume["skills"]
    assert review["keywords_added"][0]["keyword"] == "CI/CD pipelines" and "Jenkins" in review["keywords_added"][0]["evidence"]
    assert review["ats"]["score"] > review["ats"]["score_before"]
    assert [g["keyword"] for g in review["ats"]["gaps"]] == ["C#"]
