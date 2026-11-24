"""The shared workflow for every application: one evidence selection for both documents, role-type emphasis,
two letter examples, and the final check before handing back (fictional data)."""
from app import drafting, final_check, scorecard, selection, tailoring
from app.bank import bank_profile
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


def test_default_resume_writes_a_short_summary_from_the_selected_records(monkeypatch, client, fake_llm):
    import json

    monkeypatch.setattr(tailoring, "EXACT_LINES", True)
    monkeypatch.setattr(tailoring, "EXACT_SUMMARY", False)
    p = with_bank()
    client.put("/profile", json=p.model_dump(mode="json")).raise_for_status()
    data = SDET_JOB.model_dump(mode="json", exclude={"id", "created_at", "skills_source"})
    created = client.post("/jobs", json=data).raise_for_status().json()
    applied = client.post("/applications", json={"job_id": created["id"]}).raise_for_status().json()
    fake_llm.answer = {"sentences": [
        {"id": "T1", "text": "Uses REST Assured and JUnit 5 for reusable Java API tests across microservices "
                             "to improve uptime."},
        {"id": "T2", "text": "Owned Kubernetes upgrades across the entire company."},
    ]}
    response = client.post(f"/applications/{applied['id']}/drafts", json={"kind": "resume"})
    doc = response.raise_for_status().json()
    resume = json.loads(doc["content"])
    assert len(fake_llm.calls) == 1 and "EVIDENCE:" in fake_llm.calls[0]["user"]
    assert resume["summary_source"] == "composed"
    assert "Uses REST Assured and JUnit 5 for reusable Java API tests across microservices." in resume["summary"]
    assert "improve uptime" not in resume["summary"] and "entire company" not in resume["summary"]
    assert any(c["unsupported"] for c in doc["review"]["summary_checks"])
    assert all(not b["changed"] for role in resume["experience"] for b in role["bullets"])
    assert {"Java", "REST Assured", "Kubernetes"} <= set(resume["skills"])
    assert "Ollama" not in resume["skills"] and "FastAPI" not in resume["skills"]
    assert all(g["name"] != "Skills" for g in resume["skill_groups"])


def test_professional_summary_uses_reserve_evidence_to_reach_six_printed_lines(monkeypatch, fake_llm):
    from app import linefit

    profile = with_bank()
    records = {r.id: r.text for r in profile.experience_bank}
    items = [("Frameworks", [records["CH01"]]), ("Deployments", [records["CH04"]]),
             (tailoring.RESERVE + "Data validation", [records["NB03"]]),
             (tailoring.RESERVE + "Test strategy", [records["NB01"]]),
             (tailoring.RESERVE + "Mentoring", [records["NB04"]])]

    def measure(_html, candidates):
        assert all(c["kind"] == "summary" for c in candidates)
        return {c["key"]: (1 + i + (i >= 4), 0) for i, c in enumerate(candidates)}

    monkeypatch.setattr(linefit, "browser_line_measure", measure)
    fake_llm.answer = {"sentences": []}
    checks = []
    summary, _, _, _ = tailoring.professional_summary(
        fake_llm, profile, SDET_JOB, items, set(), [profile.summary], checks)
    assert records["NB01"] in summary and records["NB04"] not in summary
    assert checks[-1]["decision"] == "left out after reaching six lines"


def test_letter_keeps_the_best_example_and_never_repeats_the_application_line(fake_llm):
    p = with_bank()
    facts = drafting.build_facts(bank_profile(p))
    priorities, _ = drafting.letter_examples(p, SDET_JOB, facts, selection.plan(p, SDET_JOB))
    assert "Kubernetes" in priorities[1].requirement
    fake_llm.answer = {
        "text": "I'm applying for the Senior SDET role at Example (fictional). Senior SDET at Example\n\n"
                "I’m applying for the Senior SDET role because I developed reusable Java API automation.\n\n"
                "I'd welcome the chance to discuss the role.",
        "fact_ids_used": priorities[0].fact_ids,
    }
    letter, _ = drafting.draft(fake_llm, p, SDET_JOB, "cover_letter")
    assert letter.lower().count("applying") == 1
    assert "Senior SDET at Example" not in letter
    assert "At Contoso Health, I developed reusable Java API automation" in letter
    assert "validated Kubernetes and Helm" in letter


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
