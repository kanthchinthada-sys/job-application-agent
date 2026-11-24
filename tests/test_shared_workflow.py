"""The shared workflow for every application: one evidence selection for both documents, role-type emphasis,
two letter examples, and the final check before handing back (fictional data)."""
from app import drafting, final_check, scorecard, selection, tailoring
from app.schemas import JobOut
from tests.test_bank import FDE_JOB, LEAD_JOB, SDET_JOB, job, with_bank


def test_role_types_are_told_apart_by_title_and_work():
    names = {t: scorecard.family(t, "").name for t in (
        "QA Automation Engineer", "Senior Software Development Engineer in Test", "QA Lead", "QA Manager",
        "Forward Deployed Engineer", "Senior SDET, ML Evaluation", "Senior Automation Tester")}
    assert names["QA Automation Engineer"] == "qa_automation" and names["Senior Automation Tester"] == "qa_automation"
    assert names["Senior Software Development Engineer in Test"] == "sdet"
    assert names["QA Lead"] == "lead" and names["QA Manager"] == "manager"
    assert names["Forward Deployed Engineer"].startswith("fde") and names["Senior SDET, ML Evaluation"] == "ai_qa"


def test_one_evidence_selection_feeds_the_summary_and_the_letter():
    p = with_bank()
    plan = selection.plan(p, SDET_JOB)
    chosen = tailoring.evidence_priorities(p, SDET_JOB, plan)
    main = [e for e in chosen if e["main"]]
    assert main and all(e["match"] in ("direct", "transferable") and e["records"] for e in main)
    from app.bank import bank_profile
    facts = drafting.build_facts(bank_profile(p))           # as the letter sees your profile
    priorities, shown = drafting.letter_examples(p, SDET_JOB, facts, plan)
    assert priorities
    assert len(priorities) <= drafting.LETTER_EXAMPLES
    by_id = {f.id: f.text for f in facts}
    records = {r.id: r.text for r in p.experience_bank}
    first = [records[i] for i in main[0]["records"]]
    assert any(t in by_id[f] for f in priorities[0].fact_ids for t in first)     # the same records as the summary


def test_a_gap_in_the_screen_is_kept_as_missing_never_claimed():
    p = with_bank()
    posting = job("Senior SDET", "Required Qualifications:\nJava test automation frameworks\nRust services\n"
                  "Benefits\nGood.")
    plan = selection.plan(p, posting)
    chosen = tailoring.evidence_priorities(p, posting, plan)
    assert any(e["match"] == "missing" and "Rust" in e["ask"] for e in chosen)


def test_a_sentence_saying_why_an_example_matters_needs_no_record():
    j = SDET_JOB
    assert drafting.is_relevance_sentence("That matters for a team whose release gates must stay deterministic.", j)
    assert not drafting.is_relevance_sentence("That matters because I cut release time by 40%.", j)   # a claim
    assert not drafting.is_relevance_sentence("I built the framework.", j)


def test_the_final_check_fixes_what_a_recruiter_would_catch():
    p = with_bank()
    plan = selection.plan(p, SDET_JOB)
    resume = tailoring.TailoredResume(
        name="Fictional Person", headline=None, contact={}, summary="Built Java test automation (fictional). "
        "Built Java test automation (fictional). Write something with AI", summary_source="composed",
        skills=["REST Assured", "Oracle"], skill_groups=[{"name": "CI/CD", "skills": ["Jenkins", "Oracle"]},
                                                        {"name": "API testing", "skills": ["REST Assured", "Jenkins"]}],
        experience=[tailoring.Role(company=e.company, title=e.title, start_date=e.start_date, end_date=e.end_date,
                                   bullets=[], omitted=0) for e in p.experience],
        projects=[], education=[], certifications=[], achievements=[])
    first = resume.experience[0]
    first.bullets = [tailoring.Bullet(text="Write something with AI", original="x", changed=False),
                     tailoring.Bullet(text="Ran release suites. Fixed flaky tests.", original="x", changed=False),
                     tailoring.Bullet(text="Validated upgrades (fictional).", original="x", changed=False,
                                      source_id="CH04")]
    lead = [{"ask": "Kubernetes", "kind": "required", "match": "direct", "records": ["CH04"], "main": True}]
    report = final_check.check_resume(resume, p, SDET_JOB, lead)
    texts = [b.text for b in first.bullets]
    assert "Write something with AI" not in texts and first.bullets[0].source_id == "CH04"      # moved up
    assert "Ran release suites." in texts                                                     # one sentence
    assert resume.summary == "Built Java test automation (fictional)."                         # no repeat, no placeholder
    assert "Oracle" not in resume.skill_groups[0]["skills"]                                     # not a CI/CD tool
    assert report["first_third"]["record"] == "CH04"


def test_the_final_check_reads_the_letter_opening():
    p = with_bank()
    ok = "Dear Hiring Team,\n\nI'm applying for the Senior SDET role at Example.\n\nSincerely,\nFictional Person"
    bad = "Dear Hiring Team,\n\nI'm applying for this role.\n\nI built it. I built it.\n\nSincerely,\nFictional Person"
    posting = JobOut(**{**SDET_JOB.model_dump(), "company": "Example"})
    assert final_check.check_letter(ok, p, posting)["problems"] == []
    problems = final_check.check_letter(bad, p, posting)["problems"]
    assert any("does not name Example" in x for x in problems) and any("Said twice" in x for x in problems)
