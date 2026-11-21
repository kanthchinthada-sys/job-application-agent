"""Reading a posting's requirements, the technical-only ATS check, and the requirement -> resume map.

All postings and people here are FICTIONAL."""

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
