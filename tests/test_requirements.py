"""Reading a posting's requirements, the technical-only ATS check, and the requirement -> resume map.

All postings and people here are FICTIONAL."""

import re

from app import ats, posting, selection, scorecard
from app.vocabulary import is_people_skill, is_technical

POSTING = """Data Engineer (fictional)

Your impact

You'll build data products for our clients.

Location Hybrid 2 days per week at any Example Corp office.

Responsibilities

• Develop ETL/ELT pipelines and APIs using Python, SQL, and PySpark.
• Build reports in Power BI for client teams.

Here's what you'll need

Required

• Proficiency in Python and SQL for data transformation.
• Familiarity with the data stack: Azure Data Factory, Synapse, Fabric, Power BI, or Databricks.
• Demonstrated curiosity, initiative, and self-motivation.
• Seller-doer mindset, with an interest in client relationships and contributing to business development.

Preferred

• Experience with Jupyter notebooks and pandas.

We value collaboration and believe that in-person interactions are crucial for our culture.

Your application experience is important to us. If you need reasonable adjustments, tell us.
"""


def test_a_bare_required_heading_is_a_heading_not_a_requirement():
    req, pref, _ = posting.requirement_lines(POSTING)
    assert "Required" not in req and "Preferred" not in pref
    assert req[0].startswith("Proficiency in Python")


def test_where_and_how_you_work_is_never_a_requirement():
    req, pref, duties = posting.requirement_lines(POSTING)
    every = " ".join(req + pref + duties)
    assert "Hybrid 2 days" not in every and "in-person interactions" not in every
    assert "reasonable adjustments" not in every


def test_an_either_or_list_with_unknown_items_is_one_requirement():
    required, preferred = posting.keywords(POSTING)
    assert "Azure Data Factory/Power BI/Databricks" in required        # Synapse and Fabric don't break the list
    assert "Power BI" not in required                                  # the duty mention is covered by the list


def test_people_skills_and_ways_of_working_are_not_technical():
    for term in ["curiosity", "initiative", "self-motivation", "seller-doer mindset", "client relationships",
                 "business development", "in-person interactions", "hybrid working policy", "recruitment process",
                 "cross-functional collaboration", "stakeholder management", "collaboration"]:
        assert not is_technical(term), term
    for term in ["Python", "Power BI/Databricks", "Jupyter notebooks", "collaborative notebooks", "Palantir Foundry",
                 "data engineering workflows", "CI/CD", "version control (Git)", "test planning", "DeepEval"]:
        assert is_technical(term), term
    assert not is_people_skill("DeepEval")          # unknown is not the same as soft


def test_the_ats_check_scores_technical_keywords_only_and_shows_where_each_match_is():
    resume = "Skills: Python, Git\nBuilt pytest suites in Python for a payments API (fictional)."
    report = ats.analyze(resume, resume, ["Python", "curiosity", "hybrid working policy", "version control (Git)"], [])
    assert {k.keyword for k in report.matched} == {"Python", "version control (Git)"}
    assert report.not_scored == ["curiosity", "hybrid working policy"]
    assert report.score == 100.0                                       # soft words don't lower the score
    python = next(k for k in report.matched if k.keyword == "Python")
    assert python.evidence == "Skills: Python, Git"


def test_soft_skill_lines_are_never_gaps_and_partial_matches_say_so():
    from tests.test_scorecard import with_bank
    from app.schemas import JobOut
    p = with_bank()
    job = JobOut(id=1, title="Data Engineer (fictional)", company="Example Corp (fictional)", location=None,
                 source_url=None, description=POSTING, required_skills=["Python", "SQL"], preferred_skills=[],
                 created_at="2026-11-20T00:00:00Z")
    plan = selection.plan(p, job)
    row = lambda start: next(r for r in plan.rows if r.requirement.startswith(start))
    soft = row("Demonstrated curiosity")
    assert soft.category == "soft skill" and soft.strength in ("shown by example", "not shown")
    assert soft.requirement not in plan.gaps
    assert row("Seller-doer mindset").category == "soft skill"
    assert row("Proficiency in Python").category == "technical"
    assert not any(r.requirement.startswith(("Location", "We value", "Required")) for r in plan.rows)


def test_the_requirements_map_says_where_each_record_printed_or_that_it_was_cut():
    from app import tailoring
    from tests.test_scorecard import with_bank, SDET_JOB

    p = with_bank()
    plan = selection.plan(p, SDET_JOB)
    wp = selection.working_profile(p, plan)
    roles = [tailoring.Role(company=e.company, title=e.title, start_date=e.start_date, end_date=e.end_date, omitted=0,
                            bullets=[tailoring.Bullet(text=h, original=h, changed=False,
                                                      source_id=plan.selected[e.company][i])
                                     for i, h in enumerate(e.highlights[:1])])          # only the first one printed
             for e in wp.experience if e.company in plan.selected]
    resume = tailoring.TailoredResume(name="A", headline=None, contact={}, summary="", summary_source="composed",
                                      skills=[], experience=roles, education=[], certifications=[], achievements=[])
    rows = scorecard.requirement_map(plan, resume, wp)
    printed = [x for r in rows for x in r["on_resume"]]
    assert printed and all(x["where"].endswith("bullet 1") for x in printed)
    assert any(r["not_printed"] for r in rows)                          # the rest were cut to fit
    cats = [r["category"] for r in rows]
    assert cats == sorted(cats, key=["technical", "education", "soft skill"].index)   # technical first


# ---------- understanding a requirement, not just its words (fictional) ----------

SMILE_LIKE = """Senior Automation Tester (fictional)

About the job
Example Health (fictional) makes it easy to exchange data with our #BetterData platform.
Apply today and find plenty of reasons to join Example Health!
The Senior Automation Tester will be responsible for maintaining test automation frameworks.

Requirements:
Post-secondary education in Computer Science, Quality Assurance or similar and/or equivalent working experience.
Demonstrated hands-on experience with Java, RestAssured, Selenium, Maven, and Git.
Advanced knowledge of REST API testing, SQL, and end-to-end testing.
Experience with AI-assisted tools such as Claude Code, prompt engineering, and AI agents. Knowledge of MCP is an asset.
Strong understanding of automation best practices, OOP principles, code reviews, and debugging.
Ability to independently design, develop, and maintain scalable automation solutions.
Experience working in Agile environments, with strong analytical, problem-solving, and communication skills.

Benefits
Remote work.
"""


def _smile_profile():
    from app.schemas import BankRecord
    from tests.test_bank import with_bank
    p = with_bank()
    owner = p.experience[0].company
    extra = [
        ("ZZ01", "Built Java API automation with REST Assured and Maven, validating SQL results (fictional)."),
        ("ZZ02", "Wrote Playwright tests for checkout flows in a GitHub repository (fictional)."),
        ("ZZ03", "Built prompt-regression tests for generated content and reviewed pull requests (fictional)."),
        ("ZZ04", "Used Claude Code and AI agents to debug failing Selenium suites (fictional)."),
        ("ZZ05", "Worked in Agile sprints with standups and retrospectives (fictional)."),
    ]
    bank = list(p.experience_bank) + [BankRecord(id=i, text=t, owner=owner, owner_kind="experience",
                                                 status="verified") for i, t in extra]
    return p.model_copy(update={"experience_bank": bank, "skills": list(p.skills) + ["MCP"]})


def _smile_plan():
    from app.schemas import JobOut
    job = JobOut(id=1, title="Senior Automation Tester (fictional)", company="Example Health (fictional)",
                 location=None, source_url=None, description=SMILE_LIKE, required_skills=["Java"],
                 preferred_skills=[], created_at="2026-11-21T00:00:00Z")
    return selection.plan(_smile_profile(), job)


def test_a_skill_written_another_way_or_implied_by_your_work_is_not_a_gap():
    plan = _smile_plan()
    missing = {p for r in plan.rows for p in r.not_found_parts}
    # RestAssured = REST Assured; Git via GitHub; end-to-end via Playwright; OOP via Java; prompt engineering via
    # prompt-regression tests; MCP from the skills list
    for skill in ("rest assured", "restassured", "git", "end-to-end testing", "oop principles", "prompt engineering",
                  "mcp"):
        assert skill not in missing, (skill, missing)
    assert not plan.gaps, plan.gaps


def test_only_named_technical_skills_are_scored():
    plan = _smile_plan()
    row = lambda start: next(r for r in plan.rows if r.requirement.startswith(start))
    independent = row("Ability to independently design")
    assert independent.not_found_parts == [] and independent.strength == "direct"
    agile = row("Experience working in Agile")
    assert not {"analytical", "problem-solving"} & set(agile.not_found_parts + agile.found_parts)
    vocab = __import__("app.evidence", fromlist=["x"]).vocabulary(_smile_profile())
    q = selection.Requirement(text="Architect, implement, and estimate scalable frameworks.", kind="responsibility",
                              parts=["architect", "implement", "estimate scalable frameworks"])
    assert selection.technical_parts(q, vocab) == []


def test_the_company_describing_itself_is_not_a_duty_and_education_can_be_met_by_experience():
    plan = _smile_plan()
    texts = [r.requirement for r in plan.rows]
    assert not any("makes it easy" in t or t.startswith("Apply today") for t in texts)
    assert any(t.startswith("The Senior Automation Tester will be responsible") for t in texts)
    edu = next(r for r in plan.rows if r.requirement.startswith("Post-secondary education"))
    assert edu.category == "education" and edu.strength == "direct"


def test_a_skill_shown_only_outside_the_chosen_bullets_says_where_it_is():
    plan = _smile_plan()
    row = next(r for r in plan.rows if "MCP" in r.requirement)
    assert "mcp" not in row.not_found_parts
    assert "mcp" in row.found_parts or row.found_elsewhere.get("mcp") == "your skills list"


def test_fitting_the_page_never_swaps_out_a_skill_the_job_asks_for():
    from app import layout, tailoring
    tools = ["Awaitility", "Ginkgo", "Gomega", "Pact", "Testcontainers", "Appium", "UFT", "pytest", "Vitest",
             "Mockito", "k6", "Allure", "JUnit", "TestNG", "Selenium", "Playwright", "Cucumber", "REST Assured"]
    group = {"name": "Automation", "skills": ["end-to-end testing", "debugging"] + tools}
    resume = tailoring.TailoredResume(name="A", headline=None, contact={}, summary="", summary_source="composed",
                                      skills=[], skill_groups=[group], experience=[], education=[],
                                      certifications=[], achievements=[])
    layout.fit(resume, budget_override=0, protect=["end-to-end testing", "debugging", "Selenium"])
    kept = resume.skill_groups[0]["skills"]
    assert "end-to-end testing" in kept and "debugging" in kept and "Selenium" in kept


def test_a_summary_with_failed_sentences_stays_two_or_three_sentences_and_says_why():
    from app import tailoring
    from tests.test_bank import with_bank, SDET_JOB
    p = with_bank()
    plan = selection.plan(p, SDET_JOB)
    wp = selection.working_profile(p, plan)
    own, items, from_project, use_ai, ai_sentence = tailoring.summary_items(wp, SDET_JOB, plan)
    # every AI sentence fails its checks ("designed and owned" claims more ownership): written without AI instead
    answers = {f"T{i + 1}": "Designed and owned everything at enterprise scale for the whole company."
               for i in range(len(items))}
    checks: list = []
    summary, notes = tailoring._finish_summary(wp, SDET_JOB, own, items, from_project, use_ai, ai_sentence,
                                               answers, [], None, checks)
    n = len(re.split(r"(?<=[.!?])\s+", summary))
    assert 2 <= n <= 3, summary
    assert "enterprise scale" not in summary
    flagged = [c for c in checks if c["item"] != "opening"]
    assert flagged and all(c["unsupported"] for c in flagged)              # recorded as unsupported claims
    assert any(u["kind"] == "ownership" for c in flagged for u in c["unsupported"])


def test_a_clause_cut_for_the_summary_ends_on_a_whole_phrase():
    from app import tailoring
    head = tailoring._head("Developed reusable Java BDD automation with Cucumber and TestNG for repeatable "
                           "transformation and data-quality checks across pipelines (fictional).")
    assert head == "Developed reusable Java BDD automation with Cucumber and TestNG"


def test_a_skill_the_posting_asks_for_is_printed_even_without_a_skill_heading():
    from app.schemas import JobOut
    p = _smile_profile()
    job = JobOut(id=1, title="Senior Automation Tester (fictional)", company="Example Health (fictional)",
                 location=None, source_url=None, description=SMILE_LIKE, required_skills=["MCP", "Java"],
                 preferred_skills=[], created_at="2026-11-21T00:00:00Z")
    plan = selection.plan(p, job)
    assert "rest assured" in selection.named_skills(p, plan)          # posting wrote "RestAssured"
    skills, _ = selection.select_skills(p, job, plan)
    assert "MCP" in skills                                              # only in your flat skills list
