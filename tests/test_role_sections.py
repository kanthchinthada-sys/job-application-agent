"""Core Skills, experience bullets and summary evidence follow what each posting hires for (fictional data)."""
from app import core_skills, selection, tailoring
from app.drafting import plain_wording
from tests.test_bank import FDE_JOB, LEAD_JOB, SDET_JOB, job, with_bank


def _lines(p, posting):
    plan = selection.plan(p, posting)
    skills, _ = selection.select_skills(p, posting, plan)
    pool = list(dict.fromkeys(skills + p.skills + ["align stakeholders", "independently successful",
                                                   "Test strategy", "Defect triage", "Mentoring", "Postman",
                                                   "Selenium",
                                                   "Selenium WebDriver", "GitHub Copilot", "client-go"]))
    return core_skills.core_skills(p, posting, plan, pool)


def test_core_skills_are_three_to_five_lines_led_by_what_the_posting_hires_for():
    p = with_bank()
    _, lines, review = _lines(p, SDET_JOB)
    names = [g["name"] for g in lines]
    assert 3 <= len(lines) <= 5, lines
    assert names[0].startswith("Test automation") or names[0] == "API testing", names
    shown = [s for g in lines for s in g["skills"]]
    assert len(shown) == len({s.lower() for s in shown})                       # each skill once
    assert "align stakeholders" not in shown and "independently successful" not in shown   # not skills
    assert not ("Selenium" in shown and "Selenium WebDriver" in shown)         # one name per tool
    assert all(r["records"] or r["why"] for r in review)                       # evidence in the private review
    copilot = [g["name"] for g in lines if "GitHub Copilot" in g["skills"]]
    assert copilot in ([], ["AI testing"]), copilot                            # never under CI/CD


def test_a_leadership_skill_needs_a_record_of_that_work():
    p = with_bank()
    _, lines, _ = _lines(p, LEAD_JOB)
    lead = next((g["skills"] for g in lines if g["name"] == "Test leadership"), [])
    assert {"Mentoring", "Test strategy", "Defect triage"} <= set(lead), lead    # NB04, NB01, NB02 show that work
    assert len(lines) >= core_skills.MIN_LINES
    p.experience_bank = [r for r in p.experience_bank if not r.text.startswith(("Mentored", "Ran one-to-one"))]
    p.skills = [s for s in p.skills if s != "Mentoring"]
    _, lines, _ = _lines(p, LEAD_JOB)
    assert "Mentoring" not in [s for g in lines for s in g["skills"]]


def test_an_fde_posting_about_deployments_leads_with_kubernetes_not_test_tools():
    p = with_bank()
    _, lines, _ = _lines(p, FDE_JOB)
    first = lines[0]["name"]
    assert first in ("Kubernetes and deployment", "Languages", "Web frameworks"), lines
    assert all("client-go" not in g["skills"] or g["name"] == "Kubernetes and deployment" for g in lines)


def test_bullets_that_show_doing_the_work_lead_and_talk_or_routine_bullets_follow():
    p = with_bank()
    posting = job("Senior SDET", "Required Qualifications:\nJava test automation frameworks\nRelease risks\n"
                  "Selenium regression\n\nBenefits\nGood.")
    plan = selection.plan(p, posting)
    fab = plan.selected.get("Fabrikam Freight", [])
    if "FF01" in fab and "FF02" in fab:
        assert fab.index("FF01") < fab.index("FF02")      # a framework you built before talking about progress
    assert plan.scores.get("FF01", 0) > plan.scores.get("FF02", -99)


def test_an_internal_job_name_is_written_as_engineering_work():
    text = ("Registered checkout Playwright regression execution in Jenkins through shared DSL and Gradle "
            "configuration, with a verified successful main run (fictional).")
    out = plain_wording(text)
    assert out.startswith("Configured the checkout Playwright regression job in Jenkins using shared DSL")
    assert "verified a successful main run" in out


def test_the_summary_example_for_a_framework_ask_is_the_framework_you_built():
    p = with_bank()
    plan = selection.plan(p, SDET_JOB)
    _, items, *_ = tailoring.summary_items(p, SDET_JOB, plan)
    framework = next(c for n, c in items if "frameworks" in n.lower() and not n.startswith(tailoring.RESERVE))
    assert any("framework" in r.lower() or "REST Assured" in r for r in framework), framework


def test_a_generic_summary_sentence_is_one_that_could_sit_on_any_resume():
    posting = SDET_JOB
    assert tailoring._generic("Delivered high-quality software through collaboration and best practices.", posting)
    assert not tailoring._generic("Built Java test automation with REST Assured for API services.", posting)
    assert not tailoring._generic("Reduced ingestion time from 184 to 64 seconds.", posting)    # a number


def test_client_go_is_not_a_client_of_the_business():
    from app import evidence as ev
    hit = ev.find_part("customer-facing", [("r", "Maintained Kubernetes-client compatibility using client-go.")])
    assert hit is None


def test_a_posting_phrase_is_never_listed_as_a_skill():
    assert not core_skills.is_listable("data validation methodologies")
    assert not core_skills.is_listable("LLM-powered engineering solutions")
    assert not core_skills.is_listable("implementation")
    assert core_skills.is_listable("REST Assured") and core_skills.is_listable("ETL validation")


def test_a_sentence_naming_the_tools_of_the_work_is_not_generic():
    assert not tailoring._generic("In a personal project, built an agent using Python, FastAPI and SQLite.", LEAD_JOB)


def test_a_summary_sentence_is_repaired_before_it_falls_back_to_a_record():
    p = with_bank()
    records = ["Mentored QA colleagues on automation patterns and SQL validation (fictional).",
               "Led defect triage and presented release risks to business stakeholders (fictional)."]
    posting = job("QA Lead", "Required Qualifications:\nMentoring and best practices for scalable testing\n\n"
                  "Benefits\nGood.")
    ai = "Mentored QA colleagues on scalable best practices, SQL validation, and defect triage to improve testing standards."
    fixed = tailoring._repair_summary_sentence(ai, records, p, posting, [])
    assert fixed is not None, tailoring.summary_claim_problems(ai, records, p, posting)
    assert "improve" not in fixed[0] and "best practices" not in fixed[0] and "SQL validation" in fixed[0]
