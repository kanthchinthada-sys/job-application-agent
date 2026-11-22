"""Regression: a posting's either-or skill ("Playwright or Selenium") must never put the option you don't have on
your resume, in a letter or in an answer. Only skills your profile backs are listed. All data FICTIONAL."""

import copy
import json

import pytest

from app import ats
from app.tailoring import check_rewording, drop_unsupported_skills

POSTING = ("Senior QA Automation Engineer (fictional)\nNorthwind Payments (fictional)\n\nRequirements\n"
           "- Experience with Playwright or Selenium for UI testing.\n- CI/CD with Jenkins or GitHub Actions.\n"
           "- SQL skills for data validation.\n- AWS is a plus.\n")


@pytest.fixture
def selenium_only(profile_data):
    """Selenium experience, no Playwright, Kubernetes but no AWS."""
    p = copy.deepcopy(profile_data)
    p["skills"] = ["Python", "pytest", "SQL", "Selenium", "Jenkins", "Kubernetes"]
    p["experience"][1]["highlights"] = ["Wrote automated regression tests for a web application using Selenium and Java."]
    p["experience"][0]["highlights"].append("Ran release suites on Kubernetes through Jenkins pipelines.")
    return p


@pytest.fixture
def job_id(client, selenium_only):
    client.put("/profile", json=selenium_only)
    job = client.post("/jobs", json={
        "title": "Senior QA Automation Engineer (fictional)", "company": "Northwind Payments (fictional)",
        "description": POSTING, "required_skills": ["Playwright or Selenium", "Jenkins or GitHub Actions", "SQL skills"],
        "preferred_skills": ["AWS", "Playwright/Selenium"], "source_url": "https://jobs.example.com/fictional/pw"})
    assert job.status_code == 201, job.text
    return job.json()["id"]


def test_either_or_skills_split_into_options_and_tools_need_literal_evidence():
    assert ats.skill_terms("Playwright or Selenium") == ["Playwright", "Selenium"]
    assert ats.skill_terms("Jenkins, GitHub Actions, or GitLab CI") == ["Jenkins", "GitHub Actions", "GitLab CI"]
    assert ats.skill_terms("SQL skills") == ["SQL"] and ats.skill_terms("CI/CD pipelines") == ["CI/CD pipelines"]
    assert ats.resume_has("Skills: Selenium, Java", "Playwright or Selenium")        # one option meets it
    assert ats.supports_skill("Wrote tests with Selenium.", "Playwright") is None    # never the other option
    assert ats.supports_skill("Deployed with Kubernetes and Helm.", "AWS") is None   # related is not the tool
    assert ats.supports_skill("Ran nightly suites in Jenkins.", "CI/CD")             # a practice: equivalent work


def test_the_resume_lists_selenium_never_playwright_and_the_export_agrees(client, fake_llm, job_id):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [{"bullets": [], "sentences": []}]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    resume = json.loads(doc["content"])
    listed = resume["skills"] + [s for g in resume["skill_groups"] for s in g["skills"]]
    assert "Selenium" in listed
    assert not any("playwright" in s.lower() or " or " in s.lower() for s in listed), listed
    assert "AWS" not in listed                                       # Kubernetes doesn't show AWS
    added = [k["keyword"] for k in doc["review"]["keywords_added"]]
    assert not any("playwright" in k.lower() for k in added), added
    matched = {k["keyword"] for k in doc["review"]["ats"]["matched"]}
    assert "Playwright or Selenium" in matched                       # the requirement counts as met
    for url in (f"/documents/{doc['id']}/print", f"/documents/{doc['id']}/download"):
        assert "playwright" not in client.get(url).text.lower(), url


def test_a_reworded_bullet_may_not_gain_the_other_option_or_any_new_tool(job_id, client):
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    original = "Wrote automated regression tests for a web application using Selenium and Java."
    assert check_rewording(original, original.replace("Selenium and Java", "Selenium and Playwright"), job)
    assert check_rewording(original, original.replace("and Java", "Java and Cypress"), job)
    assert check_rewording(original, "Automated web regression tests with Selenium and Java.", job) is None


def test_the_final_check_drops_any_listed_skill_the_profile_does_not_back(selenium_only):
    from app.schemas import Profile
    from app.tailoring import TailoredResume
    cv = TailoredResume(name="A", headline=None, contact={}, summary="", summary_source="composed",
                        skills=["Selenium", "Playwright", "AWS", "Playwright or Selenium"],
                        skill_groups=[{"name": "Automation", "skills": ["Selenium", "Playwright"]}],
                        experience=[], education=[], certifications=[], achievements=[])
    removed = drop_unsupported_skills(cv, Profile(**selenium_only))
    assert cv.skills == ["Selenium"] and cv.skill_groups[0]["skills"] == ["Selenium"]
    assert set(removed) == {"Playwright", "AWS", "Playwright or Selenium"}


def test_letters_and_answers_are_flagged_when_they_claim_the_other_option(client, fake_llm, job_id):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.answer = {"text": "I build UI tests with Playwright and Selenium (fictional).", "fact_ids_used": ["F1"]}
    answer = client.post(f"/applications/{app_id}/drafts",
                         json={"kind": "screening_answer", "question": "Which UI test tools have you used?"}).json()
    assert "playwright" not in answer["content"].lower()                 # rewritten from the Selenium record
    check = answer["review"]["claim_checks"][0]
    assert any("Playwright" in x for x in check["problems"]) and "Selenium" in (check["now"] or "")

    fake_llm.answer = {"text": "I'm applying for the role. I build UI tests with Playwright every day.",
                       "fact_ids_used": ["F1"]}
    letter = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    assert "playwright" not in letter["content"].lower()             # the sentence is left out, not printed
    assert any("Playwright" in x for x in letter["review"].get("removed", []))


def test_a_summary_may_paraphrase_and_combine_but_every_claim_needs_its_evidence(job_id, client, selenium_only):
    from app.schemas import JobOut, Profile
    from app.tailoring import summary_claim_problems
    job = JobOut(**client.get(f"/jobs/{job_id}").json()).model_copy(update={
        "description": "Run regression suites in Jenkins CI/CD pipelines and report release risks. Lead a team."})
    p = Profile(**selenium_only)
    records = ["Added API regression tests to Jenkins pipelines, cutting release checks from two days to four hours.",
               "Ran release suites on Kubernetes through Jenkins pipelines."]
    kinds = lambda sentence: [x["kind"] for x in summary_claim_problems(sentence, records, p, job)]
    # natural paraphrase, combining two supported capabilities (words of its own are fine): no problem
    assert kinds("Automates API regression testing in Jenkins and keeps release suites running on Kubernetes.") == []
    # the posting's duty in its words ("risks" appears nowhere in this profile)
    assert kinds("Ran regression suites in Jenkins CI/CD pipelines to identify release risks.") == ["posting_duty"]
    assert kinds("Ran Playwright suites in Jenkins pipelines.") == ["tool"]
    assert kinds("Built and owned the release suites on Kubernetes.") == ["ownership"]
    assert kinds("Cut release checks from five days to four hours in Jenkins pipelines.") == ["metric"]
    assert kinds("Led a team that ran release suites on Kubernetes through Jenkins pipelines.") == ["leadership"]
    outcome = summary_claim_problems("Ran release suites on Kubernetes, improving deployment speed.",
                                     [records[1]], p, job)
    assert [x["kind"] for x in outcome] == ["outcome"] and "improving" in outcome[0]["detail"]
    # a different kind of result than the records report: "reproduce" is not "resolve"
    support = ["Worked with customer support to reproduce production issues using Docker containers."]
    assert [x["kind"] for x in summary_claim_problems("Resolved production issues using Docker containers.",
                                                      support, p, job)] == ["outcome"]
    assert kinds("Shortened release checks by adding API regression tests to Jenkins pipelines.") == []   # "cutting"
    employer = p.experience[1].company                    # the records above are from experience[0]
    assert "employer" in kinds(f"Ran release suites on Kubernetes for {employer} through Jenkins pipelines.")


def test_a_letter_paragraph_opening_with_this_must_say_what_it_means(client, fake_llm, job_id):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [
        {"text": "I'm applying for the role. I built a pytest suite for a payments API used by three internal teams."
                 "\n\nThis cut release checks from two days to four hours.", "fact_ids_used": ["F1"]},
        # the "fix" says the same thing again: still pointing at nothing, so it is left out
        {"fixes": [{"id": "S1", "text": "This cut release checks from two days to four hours.", "fact_ids": ["F1"]}],
         "additions": []},
    ]
    letter = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    assert "This cut release checks" not in letter["content"]
    assert any(x.startswith("This cut release checks") for x in letter["review"]["removed"])


def test_a_lone_this_paragraph_is_told_again_from_its_fact_with_the_right_employer():
    from app.drafting import Fact, drop_dangling_sentences
    facts = [Fact(id="F1", text="At Example Corp (fictional) as Senior QA Engineer: Added API regression tests to "
                                "Jenkins pipelines, cutting release checks from two days to four hours.")]
    body = ("I'm applying for the role. I built a pytest suite for a payments API.\n\n"
            "This helped reduce release checks from two days to four hours by adding API regression tests to Jenkins "
            "pipelines.\n\nI work with Python.")
    out = drop_dangling_sentences(body, facts)
    assert "This helped" not in out
    assert "At Example Corp (fictional), I added API regression tests to Jenkins pipelines" in out
    assert "This helped" not in drop_dangling_sentences(body)          # no facts: dropped rather than kept


def test_a_summary_sentence_loses_its_filler_tail_and_stray_capitals():
    from app.tailoring import _tidy_case, trim_filler
    text, cut = trim_filler("Built Go-based Kubernetes integration tests to validate workload readiness and cleanup, "
                            "supporting enterprise platform capabilities.")
    assert text == "Built Go-based Kubernetes integration tests to validate workload readiness and cleanup."
    assert cut == "supporting enterprise platform capabilities"
    assert trim_filler("Built tests, supporting QA.") == ("Built tests, supporting QA.", None)   # too little left
    assert _tidy_case("Quality engineering and Software Development professional (fictional)") == \
        "Quality engineering and software development professional (fictional)"


def _claims(sentence, profile_dict, job):
    from app.bank import bank_profile
    from app.drafting import build_facts, sentence_claims
    from app.schemas import Profile
    p = bank_profile(Profile(**profile_dict))
    found, support = sentence_claims(sentence, build_facts(p), p, job)
    return [c["kind"] for c in found]


def test_letter_claims_need_their_records_paraphrase_passes(job_id, client, selenium_only):
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    # natural paraphrase of a record, with its own employer: fine
    assert _claims("At Example Corp, I ran our release suites on Kubernetes using Jenkins pipelines.",
                   selenium_only, job) == []
    # the reported sentence: a benefit no record states
    assert _claims("I also ran release suites on Kubernetes through Jenkins pipelines, which made it easier to "
                   "catch issues before deployments.", selenium_only, job) == ["outcome"]
    # right work, wrong employer (the Kubernetes suites were at Example Corp, not Sample Systems)
    assert _claims("At Sample Systems (fictional), I ran release suites on Kubernetes through Jenkins pipelines.",
                   selenium_only, job) == ["employer"]
    assert _claims("I designed and owned the release pipelines on Kubernetes.", selenium_only, job) == ["ownership"]
    assert _claims("I led the team that ran release suites on Kubernetes.", selenium_only, job) == ["leadership"]


def test_a_letter_sentence_with_an_invented_benefit_is_rewritten_from_its_record(client, fake_llm, job_id):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    claim = ("I built a pytest suite for a payments API used by three internal teams, which made it easier to catch "
             "issues before deployments.")
    wrong = "At Sample Systems (fictional), I ran release suites on Kubernetes through Jenkins pipelines."
    fake_llm.queue = [{"text": f"I'm applying for the role. {claim}\n\n{wrong}", "fact_ids_used": ["F1"]},
                      {"fixes": [], "additions": []}]
    letter = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    body = letter["content"]
    assert "easier to catch" not in body and "Sample Systems (fictional), I ran" not in body
    assert "I built a pytest suite for a payments API used by three internal teams." in body     # the activity stays
    checks = {c["sentence"]: c for c in letter["review"]["claim_checks"]}
    assert checks[claim]["action"].startswith("kept what you did")
    assert any("outcome" in p for p in checks[claim]["problems"])
    assert checks[wrong]["now"].startswith("At Example Corp (fictional), I ran release suites")   # right employer
    assert any("employer" in p for p in checks[wrong]["problems"])
    assert checks[wrong]["checked_against"][0].startswith("At Example Corp (fictional)")
    for url in (f"/documents/{letter['id']}/print", f"/documents/{letter['id']}/download"):
        text = client.get(url).text
        assert "easier to catch" not in text and "I built a pytest suite for a payments API" in text, url


def test_an_answer_with_an_invented_benefit_is_fixed_without_another_model_call(client, fake_llm, job_id):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [{"text": "I built a pytest suite for a payments API used by three internal teams. I also ran "
                               "release suites on Kubernetes through Jenkins pipelines, which made it easier to catch "
                               "issues.", "fact_ids_used": ["F1"]}]
    answer = client.post(f"/applications/{app_id}/drafts",
                         json={"kind": "screening_answer", "question": "Tell us about CI."}).json()
    assert answer["content"].endswith("I also ran release suites on Kubernetes through Jenkins pipelines.")
    assert len(fake_llm.calls) == 1                     # the answer: one call; the claim fix needed none


def test_without_ai_unrelated_records_are_not_forced_into_one_sentence():
    from app.tailoring import _fallback_merge
    # unrelated work: the stronger (first) record alone, not "Ran release suites ..., and worked with customer support"
    assert _fallback_merge(["Ran release suites on Kubernetes through Jenkins pipelines (fictional).",
                            "Worked with customer support to reproduce production issues using Docker containers."]) \
        == "Ran release suites on Kubernetes through Jenkins pipelines (fictional)."
    # related work (both about API tests) still reads as one sentence
    both = _fallback_merge(["Added API regression tests to Jenkins pipelines for the payments service (fictional).",
                            "Wrote contract tests for the payments API with Pact (fictional)."])
    assert ", and wrote contract tests" in both


def test_routine_practice_is_not_one_of_the_few_summary_sentences():
    from app import tailoring
    assert tailoring._ROUTINE.search("Maintained automation code in GitLab using feature branches, commits, and "
                                     "merge requests (fictional).")
    assert not tailoring._ROUTINE.search("Integrated pytest and Playwright into GitHub Actions with isolated "
                                         "databases (fictional).")


def test_a_summary_sentence_never_mixes_employer_and_personal_project_work():
    from app import selection, tailoring
    from tests.test_bank import FDE_JOB, SDET_JOB, with_bank
    p = with_bank()
    for job in (FDE_JOB, SDET_JOB):
        plan = selection.plan(p, job)
        wp = selection.working_profile(p, plan)
        own, items, from_project, *_ = tailoring.summary_items(wp, job, plan)
        kind = {r.text: r.owner_kind for r in wp.experience_bank}
        for name, records in items:
            if name == tailoring.OPENING:
                continue
            kinds = {kind.get(r, "experience") for r in records}
            assert len(kinds) == 1, (name, records)
            assert ("project" in kinds) == (name in from_project)    # project work is labelled as such


def test_a_retold_record_keeps_its_owner_and_says_one_thing():
    from app.drafting import Fact, _fact_sentence
    assert _fact_sentence(Fact(id="F1", text="Project Agent (fictional): Built a local job agent with FastAPI.")) \
        == "In a personal project, I built a local job agent with FastAPI."
    assert _fact_sentence(Fact(id="F2", text="At Example Corp (fictional) as QA: Maintained suites in GitLab. "
                                             "Reviewed test changes in merge requests.")) \
        == "At Example Corp (fictional), I maintained suites in GitLab."           # its first sentence only


def test_project_work_told_as_employer_work_is_caught_and_closings_are_never_retold(job_id, client, selenium_only):
    from app.bank import bank_profile
    from app.drafting import build_facts, grounded_rewrite, sentence_claims
    from app.schemas import JobOut, Profile
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    data = dict(selenium_only, projects=[{"name": "Release Bot (fictional)", "description": "A chat bot for releases.",
                                          "highlights": ["Built a release bot in Python that posts Jenkins results to chat."]}])
    p = bank_profile(Profile(**data))
    facts = build_facts(p)
    sentence = "I also built a release bot in Python that posts Jenkins results to chat."
    found, _ = sentence_claims(sentence, facts, p, job)
    assert [c["kind"] for c in found] == ["employer"] and "personal project" in found[0]["detail"]
    assert grounded_rewrite(sentence, facts, p, job, "")[0] == ("In a personal project, I built a release bot in "
                                                                "Python that posts Jenkins results to chat.")
    assert grounded_rewrite("I'd like to discuss how my Jenkins work could support your release goals.",
                            facts, p, job, "") is None


# --- the final accuracy pass: audience, responsibility, measures, tool context, letter shape, answers ----------
@pytest.fixture
def banked(selenium_only):
    """The Selenium-only profile with a verified experience bank (record ids, a measured result, a project)."""
    p = copy.deepcopy(selenium_only)
    p["projects"] = [{"name": "Release Bot (fictional)", "description": "A chat bot for releases.",
                      "highlights": ["Built a release bot in Python that posts Jenkins results to chat."]}]
    rec = lambda i, owner, text, kind="experience": {"id": i, "owner": owner, "owner_kind": kind, "text": text,
                                                     "status": "verified"}
    ex, ss = "Example Corp (fictional)", "Sample Systems (fictional)"
    p["experience_bank"] = [
        rec("EX01", ex, "Built a pytest suite for a payments API used by three internal teams."),
        rec("EX02", ex, "Worked with customer support to reproduce production issues using Docker containers."),
        rec("EX03", ex, "Ran release suites on Kubernetes through Jenkins pipelines."),
        rec("EX04", ex, "Reduced log upload time from 120.5 seconds to an average of 42.0 seconds, approximately 65%, "
                        "while preserving the same 2,048,000-document workload."),
        rec("SS01", ss, "Wrote automated regression tests for a web application using Selenium and Java."),
        rec("SS02", ss, "Registered checkout Selenium regression execution in Jenkins through shared DSL and Gradle "
                        "configuration, with a verified successful master run."),
        rec("SS03", ss, "Validated source-to-target mappings with SQL queries before each release."),
        rec("PR01", "Release Bot (fictional)", "Built a release bot in Python that posts Jenkins results to chat.",
            "project"),
    ]
    return p


def _facts(profile_dict):
    from app.bank import bank_profile
    from app.drafting import build_facts
    from app.schemas import Profile
    p = bank_profile(Profile(**profile_dict))
    return build_facts(p), p


def test_customer_support_is_not_customers_and_release_readiness_needs_a_record(job_id, client, selenium_only):
    """The reported opening: "worked with customer support" became "work with customers on release readiness".
    Your own summary line says it, but no work record does: the summary is never evidence for it."""
    from app.drafting import grounded_rewrite
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    opening = "I build API test automation in Python and work with customers on release readiness."
    assert "customers on release readiness" in selenium_only["summary"]
    kinds = _claims(opening, selenium_only, job)
    assert "audience" in kinds and "responsibility" in kinds
    facts, p = _facts(selenium_only)
    assert grounded_rewrite(opening, facts, p, job, "")[0] == "I build API test automation in Python."
    # short phrases count too: one word changes the audience
    assert _claims("I reproduced production issues for customers using Docker containers.", selenium_only, job) \
        == ["audience"]
    # supported paraphrases still pass
    assert _claims("I worked with our customer support team to reproduce production issues using Docker containers.",
                   selenium_only, job) == []
    assert _claims("At Example Corp, I worked with customer support to reproduce production issues in Docker.",
                   selenium_only, job) == []
    # a responsibility a record states is fine
    with_record = copy.deepcopy(selenium_only)
    with_record["experience"][0]["highlights"].append("Supported release-readiness decisions with documented results.")
    assert _claims("I supported release-readiness decisions with documented test results.", with_record, job) == []


def test_a_measurement_keeps_its_unit_and_its_employer(job_id, client, banked):
    """The reported FDE sentence: "... while preserving the same 2,048,000." lost "-document workload"."""
    from app.drafting import grounded_rewrite
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    cut = ("At Example Corp, I reduced log upload time from 120.5 seconds to an average of 42.0 seconds, "
           "approximately 65%, while preserving the same 2,048,000.")
    assert _claims(cut, banked, job) == ["metric"]
    facts, p = _facts(banked)
    fixed, how = grounded_rewrite(cut, facts, p, job, "")
    assert fixed.endswith("while preserving the same 2,048,000-document workload.") and "measure" in how
    # the measure said another way passes; so does the full record
    assert _claims("At Example Corp, I cut log upload time from 120.5 seconds to 42.0 seconds on average, about 65%, "
                   "for the same 2,048,000 documents.", banked, job) == []
    assert _claims(fixed, banked, job) == []
    assert _claims(fixed.replace("At Example Corp", "At Sample Systems (fictional)"), banked, job) == ["employer"]


def test_a_tool_is_told_with_the_work_it_was_used_for(job_id, client, banked):
    from app import claims
    from app.drafting import grounded_rewrite
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    mixed = ("I ran release suites through CI/CD pipelines using Jenkins and SQL, and I validated source-to-target "
             "mappings.")
    assert [c["kind"] for c in claims.tool_context_problems(mixed)] == ["tool_context"]
    facts, p = _facts(banked)
    fixed = grounded_rewrite(mixed, facts, p, job, "")[0]
    assert "pipelines using Jenkins, and" in fixed and fixed.endswith("mappings with SQL.")
    assert claims.tool_context_problems("I bring experience with pytest, Selenium, Jenkins, and SQL.") == []
    assert claims.tool_context_problems("I validated data pipelines using SQL and Jenkins jobs.") == []


def test_a_letter_has_a_paragraph_per_employer_projects_apart_and_record_ids_in_the_review(client, fake_llm, job_id,
                                                                                         banked):
    client.put("/profile", json=banked)
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    body = ("I'm applying for the Senior QA Automation Engineer role at Northwind Payments. I build API test automation "
            "in Python and work with customers on release readiness.\n\n"
            "At Example Corp, I built a pytest suite for a payments API used by three internal teams. I wrote automated "
            "regression tests for a web application using Selenium and Java at Sample Systems. I also built a release "
            "bot in Python as a personal project that posts Jenkins results to chat.\n\n"
            "I ran release suites on Kubernetes through Jenkins pipelines. I registered checkout Selenium regression "
            "execution in Jenkins at Sample Systems through shared DSL and Gradle configuration, with a verified "
            "successful master run.\n\n"
            "I'd welcome the chance to talk about the role.")
    fake_llm.queue = [{"text": body, "fact_ids_used": ["F1"]}, {"fixes": [], "additions": []}]
    letter = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    paras = letter["content"].split("\n\n")[1:-1]          # without the greeting and the signature
    assert paras[0].endswith("I build API test automation in Python.")              # the opening, kept true
    example = next(p for p in paras if "Example Corp" in p)
    sample = next(p for p in paras if "Sample Systems" in p)
    project = next(p for p in paras if "release bot" in p)
    assert example.startswith("At Example Corp") and "Sample Systems" not in example and "release bot" not in example
    assert "Kubernetes through Jenkins pipelines" in example                         # its own employer's paragraph
    assert sample.startswith("At Sample Systems") and sample.count("Sample Systems") == 1
    assert "configured the checkout Selenium regression job in Jenkins using shared DSL" in sample
    assert project.startswith("In a personal project") or "personal project" in project.split(".")[0]
    assert paras.index(project) > max(paras.index(example), paras.index(sample))      # after the employers
    assert len(letter["content"].split()) <= 320
    checks = letter["review"]["claim_checks"]
    opening = next(c for c in checks if "customers" in c["sentence"])
    assert any("audience" in x for x in opening["problems"]) and opening["now"] == "I build API test automation in Python."
    assert {r["id"] for r in opening["records"]} & {"EX01", "EX02", "EX03"}          # checked against these records
    assert all(r["id"] in {"EX01", "EX02", "EX03", "EX04", "SS01", "SS02", "SS03", "PR01"} or r["id"].startswith("F")
               for c in checks for r in c["records"] + c["now_records"])
    reworded = next(c for c in checks if c["sentence"].startswith("I registered"))
    assert reworded["now_records"][0]["id"] == "SS02"                                 # the record behind the wording
    import html
    printed = html.unescape(client.get(f"/documents/{letter['id']}/print").text)
    text = client.get(f"/documents/{letter['id']}/download").text
    for p in paras:
        assert p in text and p.split(". ")[0] in printed


def test_a_screening_answer_says_what_you_did_with_an_example(client, fake_llm, job_id, banked):
    client.put("/profile", json=banked)
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [{"text": "I have used Selenium for UI testing. These tools are integrated into CI pipelines, where "
                               "test scripts are executed automatically on code changes.", "fact_ids_used": ["F1"]}]
    answer = client.post(f"/applications/{app_id}/drafts",
                         json={"kind": "screening_answer", "question": "How do you run UI tests in CI?"}).json()
    content = answer["content"]
    assert "are executed automatically" not in content and content.startswith("I have used Selenium for UI testing.")
    assert "Example Corp" in content or "Sample Systems" in content                  # where you did it
    passive = next(c for c in answer["review"]["claim_checks"] if c["sentence"].startswith("These tools"))
    assert any("what you did" in p for p in passive["problems"])
    assert len(fake_llm.calls) == 1                                                  # no extra model call


def test_tools_from_different_jobs_are_never_joined_into_one_piece_of_work(job_id, client, selenium_only):
    """A live answer said "I ran Selenium tests through Jenkins pipelines": Selenium was at Sample Systems,
    Jenkins at Example Corp. And "At Contoso, I used Selenium" when Selenium was at another job."""
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    assert _claims("I ran Selenium tests through Jenkins pipelines as part of regression suites.", selenium_only,
                   job) == ["employer"]
    assert _claims("At Example Corp, I used Selenium for UI testing.", selenium_only, job) == ["employer"]
    # a plain list of what you know may name tools from any job; each one at its own job is fine
    assert _claims("I bring experience with pytest, Selenium, Jenkins, and Docker.", selenium_only, job) == []
    assert _claims("At Sample Systems, I wrote automated regression tests using Selenium and Java.", selenium_only,
                   job) == []


def test_an_answer_names_the_employer_of_each_example(client, fake_llm, job_id, selenium_only):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [{"text": "At Sample Systems, I wrote automated regression tests for a web application using "
                               "Selenium and Java. I also ran release suites on Kubernetes through Jenkins pipelines.",
                       "fact_ids_used": ["F1"]}]
    answer = client.post(f"/applications/{app_id}/drafts",
                         json={"kind": "screening_answer", "question": "How do you run tests in CI?"}).json()
    assert "At Example Corp, I ran release suites on Kubernetes through Jenkins pipelines." in answer["content"]
    assert any("follows your Sample Systems" in p for c in answer["review"]["claim_checks"] for p in c["problems"])


def test_cutting_a_sentence_never_cuts_a_list_and_tools_move_to_their_work():
    from app import claims
    from app.drafting import _supported_heads
    assert _supported_heads("I presented test status and risks to stakeholders, maintaining transparency across "
                            "teams.") == ["I presented test status and risks to stakeholders."]
    assert _supported_heads("I build API test automation in Python and work with customers on release "
                            "readiness.") == ["I build API test automation in Python."]
    assert _supported_heads("I integrated pytest and Playwright into GitHub Actions with isolated databases, "
                            "controlled model responses, and retained failure artifacts.") == []
    assert claims.move_misplaced_tools(
        "I maintain CI/CD pipelines, automate testing for distributed systems, and validate data pipelines using "
        "Jenkins, GitHub Actions, and SQL.") == ("I maintain CI/CD pipelines with Jenkins and GitHub Actions, automate "
                                                 "testing for distributed systems, and validate data pipelines using SQL.")


def test_a_reworded_result_passes_when_its_record_reports_that_kind_of_result(job_id, client, banked):
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    assert _claims("At Example Corp, I cut log upload time from 120.5 seconds to 42.0 seconds on average, about 65%, "
                   "while keeping the same 2,048,000-document workload.", banked, job) == []
    facts, p = _facts(banked)
    from app.drafting import sentence_problems
    assert sentence_problems("At Example Corp, I cut log upload time from 120.5 seconds to 42.0 seconds on average, "
                             "about 65%, while keeping the same 2,048,000-document workload.", facts, p, job) == []


def test_another_jobs_work_in_an_answer_is_not_told_as_an_example_of_the_one_before(client, fake_llm, job_id,
                                                                                   selenium_only):
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.queue = [{"text": "At Sample Systems, I wrote automated regression tests for a web application using "
                               "Selenium and Java. For example, I ran these suites on Kubernetes through Jenkins "
                               "pipelines.", "fact_ids_used": ["F1"]}]
    answer = client.post(f"/applications/{app_id}/drafts",
                         json={"kind": "screening_answer", "question": "How do you run tests in CI?"}).json()
    assert answer["content"].endswith("At Example Corp, I ran suites on Kubernetes through Jenkins pipelines.")


def test_an_opening_line_claims_only_what_you_did_with_each_tool_at_its_work(job_id, client, banked):
    """The SDET opening: "I build and maintain CI/CD pipelines, ... and validate data pipelines using Jenkins,
    GitHub Actions, and SQL" claimed more ownership and told the CI tools as data work."""
    from app.drafting import grounded_rewrite
    from app.schemas import JobOut
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    facts, p = _facts(banked)
    opening = ("I build and run release suites on Kubernetes, and validate source-to-target mappings using "
               "Jenkins and SQL.")
    fixed, how = grounded_rewrite(opening, facts, p, job, "", pi=0, si=1)
    assert fixed == ("I run release suites on Kubernetes with Jenkins, and validate source-to-target mappings using "
                     "SQL."), fixed
    assert "only the part you did" in how and "each tool" in how
    # a verb the records don't use is not a paraphrase of "ran": the sentence is retold from its record instead
    fixed, _ = grounded_rewrite(opening.replace("run", "maintain"), facts, p, job, "", pi=0, si=1)
    assert "maintain" not in fixed


def test_the_line_naming_the_job_is_never_removed_as_a_claim(job_id, client, selenium_only):
    from app.drafting import build_facts, sentence_problems
    from app.schemas import JobOut, Profile
    job = JobOut(**client.get(f"/jobs/{job_id}").json())
    p = Profile(**selenium_only)
    assert sentence_problems("I'm applying for the QA Lead role at Northshore Health Systems.", build_facts(p), p,
                             job) == []
