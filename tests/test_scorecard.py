"""Recruiter-style selection: role family, bullet strength, ownership, the scorecard and the private review.
FICTIONAL data only."""

import json

import pytest

from app import evidence as ev
from app import scorecard, selection
from app.tailoring import check_rewording
from tests.test_bank import FDE_JOB, LEAD_JOB, SDET_JOB, job, with_bank


@pytest.mark.parametrize("title, description, expected", [
    ("Forward Deployed Engineer", "Deploy on Kubernetes with Helm, troubleshoot customer clusters, observability.",
     "fde_infra"),
    ("Forward Deployed Engineer", "Build product features in Python and TypeScript with customers.", "fde_app"),
    ("Senior SDET", "Build test automation frameworks with Playwright and REST Assured in CI/CD.", "sdet"),
    ("QA Lead", "Own test strategy and release readiness; mentor the team.", "lead"),
    ("ETL Test Lead", "Lead ETL data validation and reconciliation in SQL, Databricks, data warehouse checks.",
     "data_lead"),
])
def test_role_family_comes_from_the_posting(title, description, expected):
    assert scorecard.family(title, description).name == expected


def test_a_measured_result_beats_a_scope_beats_a_duty():
    measured = "Reduced log upload time from 184 seconds to 64 seconds (fictional)."
    scope = "Developed a 29-test Playwright suite for troubleshooting workflows (fictional)."
    duty = "Responsible for regression testing (fictional)."
    assert scorecard.proof(measured)[1] == "measured result"
    assert scorecard.proof(scope)[1] == "measured scope"
    assert scorecard.proof(duty)[1] == "duty"
    assert scorecard.strength(measured) > scorecard.strength(scope) > scorecard.strength(duty)


def test_ownership_comes_from_the_verb_you_used():
    assert scorecard.ownership("Built a Selenium framework (fictional).")[1] == "owner"
    assert scorecard.ownership("Developed API tests (fictional).")[1] == "hands-on"
    assert scorecard.ownership("Contributed to the framework (fictional).")[1] == "supporting"


def test_a_rewording_may_not_claim_more_ownership():
    sdet = job("Senior SDET", "Required Qualifications:\nJava\n\nBenefits\nGood.")
    reason = check_rewording("Contributed enhancements to the automation framework (fictional).",
                             "Led enhancements to the automation framework (fictional).", sdet)
    assert reason and "ownership" in reason
    assert check_rewording("Developed API tests for the payments service (fictional).",
                           "Developed API test automation for the payments service (fictional).", sdet) is None


def test_go_no_go_is_a_release_decision_not_the_go_language():
    parts = ev.requirement_parts("Experience assessing release readiness and providing go/no-go recommendations",
                                 {"go", "release readiness"})
    assert "go" not in parts and "release readiness" in parts and "go-no-go" in parts
    assert "mentor" in ev.requirement_parts("Proven ability to mentor and coach engineers", set())


def test_pay_benefits_and_onboarding_plans_are_not_requirements():
    posting = job("Senior SDET", "Required Qualifications:\nJava test automation\n"
                  "For this role, the estimated annual base salary range is 100,000 to 120,000\n"
                  "First 30 Days\nDevelop a strong understanding of the platform architecture\n\nBenefits\nGood.")
    texts = [q.text for q in selection.requirements(posting, with_bank())]
    assert any("Java" in t for t in texts)
    assert not any(re in t.lower() for t in texts for re in ("salary", "first 30 days", "strong understanding"))


def test_soft_skills_never_decide_which_bullet_leads_a_role():
    soft_heavy = job("Forward Deployed Engineer", "Required Qualifications:\nClear communication with stakeholders\n"
                     "Collaboration and ownership\nKubernetes deployments\n\nBenefits\nGood.")
    plan = selection.plan(with_bank(), soft_heavy)
    first = plan.selected["Contoso Health"][0]
    assert first == "CH04"                       # the Kubernetes deployment bullet, not the "worked with" one


def test_the_private_review_names_the_resume_line_and_stays_off_the_resume(client, fake_llm):
    p = with_bank()
    client.put("/profile", json=json.loads(p.model_dump_json()))
    job_id = client.post("/jobs", json={"title": "Senior SDET", "company": "Example (fictional)",
                                        "description": SDET_JOB.description, "required_skills": ["Java"],
                                        "allow_duplicate": True}).json()["id"]
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [{"bullets": []}]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    sc = doc["review"]["scorecard"]
    assert sc["role_family"].startswith("SDET")
    assert 3 <= len(sc["criteria"]) <= 5
    java = next(c for c in sc["criteria"] if c["requirement"].startswith("Java test automation"))
    assert java["evidence"] == "direct" and "REST Assured" in java["resume_line"] and java["where"]
    assert "what the hiring team" not in doc["content"].lower() and "scorecard" not in doc["content"].lower()
    printed = client.get(f"/documents/{doc['id']}/print").text
    assert "recruiter might pass" not in printed and sc["my_interpretation"] not in printed


def _lead_resume(client, fake_llm, answer):
    p = with_bank()
    client.put("/profile", json=json.loads(p.model_dump_json()))
    job_id = client.post("/jobs", json={"title": "QA Lead", "company": "Example (fictional)",
                                        "description": LEAD_JOB.description, "required_skills": ["Test strategy"],
                                        "allow_duplicate": True}).json()["id"]
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [answer]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    resume = json.loads(doc["content"])
    bullets = [b["text"].rstrip(".") for role in resume["experience"] for b in role["bullets"]]
    return resume["summary"], bullets, doc["review"]


def test_without_ai_the_summary_never_repeats_a_bullet(client, fake_llm):
    summary, bullets, _ = _lead_resume(client, fake_llm, {"bullets": []})
    sentences = [x for x in summary.replace("!", ".").split(". ") if x.strip()]
    assert len(sentences) >= 2, summary
    assert not any(b in summary for b in bullets), "a summary sentence repeats a bullet word for word"


def test_the_summary_is_framed_for_the_job_and_drops_a_sentence_that_copies_a_bullet(client, fake_llm):
    opening = ("Quality engineer with 9+ years of experience across banking and healthcare (fictional), "
               "focused on test strategy and defect triage.")
    good = "Ran defect triage for each release and brought the risks it surfaced to business stakeholders (fictional)."
    summary, bullets, review = _lead_resume(client, fake_llm, {"bullets": [], "sentences": [
        {"id": "T1", "text": opening}, {"id": "T3", "text": good}]})        # T3: the defect-triage ask
    assert summary.startswith("Quality engineer with 9+ years") and "focused on test strategy" in summary
    assert good in summary                                                  # a valid rewording is kept
    copy = "Defined test strategies, closure criteria and testing priorities (fictional)."
    summary, bullets, review = _lead_resume(client, fake_llm, {"bullets": [], "sentences": [
        {"id": "T1", "text": opening}, {"id": "T2", "text": copy}]})        # T2: the test-strategy ask
    assert copy not in summary                                              # it only repeated a bullet
    entry = next(c for c in review["summary_checks"] if c["ai_sentence"] == copy)
    assert entry["style"] == ["repeats a bullet"] and entry["unsupported"] == []   # style, not an invented claim


def test_different_postings_change_the_evidence_not_just_the_keywords():
    p = with_bank()
    fde, sdet, lead = (selection.plan(p, j) for j in (FDE_JOB, SDET_JOB, LEAD_JOB))
    tops = [tuple(x.selected["Contoso Health"][:2]) for x in (fde, sdet)]
    assert tops[0] != tops[1]
    assert lead.selected["Northwind Bank"][0] in {"NB01", "NB02"}       # the lead role opens with leadership work
    assert {fde.family, sdet.family, lead.family} == {"fde_app", "sdet", "lead"}


# ---------- the summary answers what the job mainly asks (5-6 sentences, at least 6 printed lines) ----------

DUTIES_JOB = job("Senior Quality Engineer", (
    "About This Role\nAs a Senior Quality Engineer you will join a delivery team (fictional).\n"
    "Location: Remote (Springfield)\nEmployment type: Permanent, full-time\n"
    "These are some of the key components to the position:\n"
    "Coach and mentor the team for best test practices.\n"
    "Define the test strategy and test planning for each release.\n"
    "Lead defect triage with the developers.\n"
    "What will you bring to the team?\n"
    "Java test automation for REST APIs (tools like Postman are a plus)\n"
    "Release risks reporting to stakeholders\n\nBenefits\nGood."))


def test_a_location_line_does_not_hide_the_duties_and_a_question_heading_starts_the_requirements():
    from app import posting
    req, pref, duties = posting.requirement_lines(DUTIES_JOB.description)
    assert "Coach and mentor the team for best test practices." in duties
    assert any(l.startswith("Define the test strategy") for l in duties)
    assert any(l.startswith("Java test automation") for l in req)          # "(... are a plus)" is only the tools
    assert not any("Java test automation" in l for l in pref + duties)
    assert not any("Employment type" in l for l in req + pref + duties)


def test_the_summary_gives_each_main_ask_its_own_sentence_including_mentoring():
    from app import tailoring
    p = with_bank()
    plan = selection.plan(p, DUTIES_JOB)
    _, items, *_ = tailoring.summary_items(p, DUTIES_JOB, plan)
    main = [name for name, _ in items if name != tailoring.OPENING and not name.startswith(tailoring.RESERVE)]
    assert len(main) >= 3, main
    assert all("the job asks: " in name for name in main if not name.startswith("proof point"))
    assert any("mentor" in name.lower() for name in main), main            # a listed coaching duty is a main ask
    assert not any(";" in name.split("the job asks:")[1] for name in main if "the job asks:" in name)


def test_the_summary_is_at_least_six_lines_when_the_records_allow(client, fake_llm):
    from app import tailoring
    p = with_bank()
    _, items, *_ = tailoring.summary_items(p, LEAD_JOB, selection.plan(p, LEAD_JOB))
    # an AI sentence for every item (each restating its own records, so the claim checks pass)
    sentences = [{"id": f"T{i + 1}", "text": c[0]} for i, (_, c) in enumerate(items)]
    summary, bullets, review = _lead_resume(client, fake_llm, {"bullets": [], "sentences": sentences})
    count = len([x for x in summary.split(". ") if x.strip()])
    assert 5 <= count <= 6 or tailoring.summary_lines(summary) >= tailoring.SUMMARY_MIN_LINES, summary
    assert count <= tailoring.SUMMARY_SENTENCES[1]


def test_a_summary_sentence_cannot_claim_scope_or_design_that_built_records_dont_state():
    from app.tailoring import summary_claim_problems
    p = with_bank()
    records = ["Built a TypeScript front end connected through FastAPI endpoints (fictional).",
               "Built a local job-application agent using Python, FastAPI and SQLite (fictional)."]
    kinds = lambda s: [x["kind"] for x in summary_claim_problems(s, records, p, LEAD_JOB)]
    assert "scope" in kinds("Built everything at enterprise scale for the whole company.")
    assert "ownership" in kinds("Designed a TypeScript front end connected through FastAPI endpoints.")
    # a paraphrase in other words is fine
    assert kinds("Created a local job-application agent in Python with a TypeScript front end over FastAPI.") == []
