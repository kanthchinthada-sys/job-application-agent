"""Line fitting: gappy lines are fixed by honest content changes. FICTIONAL data; the browser is faked."""
from datetime import datetime

from app import linefit
from app.schemas import JobOut, Profile
from app.tailoring import Bullet, Role, TailoredResume

JOB = JobOut(id=1, title="SDET", company="Example Corp", description="FICTIONAL. Test APIs with Python.",
             required_skills=["Python"], preferred_skills=[], created_at=datetime.now())


def fake_measure(perfect: set[str]):
    """Lines: 2 for everything. Gap: 0 for texts in `perfect`, else 5px (gappy)."""
    def measure(html, items):
        return {i["key"]: (2, 0.0 if i["text"] in perfect else 5.0) for i in items}
    return measure


def resume(bullet: str, skills: list[str]) -> TailoredResume:
    return TailoredResume(
        name="Test Person", headline=None, contact={}, summary="FICTIONAL.", summary_source="composed",
        skills=list(skills), skill_groups=[{"name": "Automation", "skills": list(skills)}],
        experience=[Role(company="Example Corp (fictional)", title="QA", start_date="2020-01", end_date=None,
                         omitted=0, bullets=[Bullet(text=bullet, original=bullet, changed=False)])],
        education=[], certifications=[], achievements=[])


def profile(profile_data) -> Profile:
    profile_data["experience"][0]["company"] = "Example Corp (fictional)"
    profile_data["experience_bank"] = [
        {"id": "EX01", "text": "FICTIONAL: Ran API tests in Jenkins with pytest.", "owner": "Example Corp (fictional)",
         "owner_kind": "experience", "status": "verified"}]
    return Profile(**profile_data)


class FillLLM:
    def __init__(self, variants):
        self.variants, self.calls = variants, []

    def generate_json(self, system, user, schema):
        self.calls.append(user)
        return {"items": [{"id": "L1", "variants": self.variants}]}


def test_skill_line_is_reordered_and_bullet_refilled(profile_data):
    r = resume("Built API tests in Python for the payments service.", ["pytest", "Selenium", "Playwright"])
    better_bullet = "Built API tests in Python and pytest for the payments service."
    llm = FillLLM([better_bullet])
    rep = linefit.polish(llm, r, profile(profile_data), JOB, lambda x: "<html></html>",
                         measure=fake_measure({"pytest, Playwright, Selenium", better_bullet}))
    assert r.skill_groups[0]["skills"] == ["pytest", "Playwright", "Selenium"]   # first (most relevant) stays first
    assert r.experience[0].bullets[0].text == better_bullet and r.experience[0].bullets[0].changed
    assert len(rep["changes"]) == 2 and rep["gappy_before"] == 2


def test_variant_with_a_tool_you_did_not_use_there_is_rejected(profile_data):
    r = resume("Built API tests in Python for the payments service.", ["pytest"])
    bad = "Built API tests in Python and Kubernetes for the payments service."
    rep = linefit.polish(FillLLM([bad]), r, profile(profile_data), JOB, lambda x: "",
                         measure=fake_measure({bad}))
    assert r.experience[0].bullets[0].text.startswith("Built API tests in Python for")
    assert "kubernetes" in rep["variants_rejected"][0]["reason"]


def test_never_adds_a_line(profile_data):
    r = resume("Built API tests in Python for the payments service.", ["pytest"])
    longer = "Built API tests in Python with pytest for the payments service."
    def measure(html, items):
        return {i["key"]: ((3, 0.0) if i["text"] == longer else (2, 5.0)) for i in items}
    linefit.polish(FillLLM([longer]), r, profile(profile_data), JOB, lambda x: "", measure=measure)
    assert r.experience[0].bullets[0].changed is False


def test_check_fill_keeps_tools_and_job_keywords():
    orig = "Built API tests in Python with pytest for the payments service."
    assert linefit.check_fill(orig, "Built API tests with pytest for the payments service.", "", JOB) == "drops: python"
    assert linefit.check_fill(orig, "Built API tests in Python for the payments service.", "", JOB) == "drops: pytest"
    assert linefit.check_fill(orig, "Built API tests in Python with pytest for the payment service.", "", JOB) is None


def test_no_browser_means_no_changes(profile_data):
    r = resume("Built API tests.", ["pytest"])
    rep = linefit.polish(None, r, profile(profile_data), JOB, lambda x: "", measure=lambda h, i: None)
    assert rep["measured"] is False and rep["changes"] == []


def test_fit_measured_keeps_the_fullest_version_that_prints_exactly_two_pages():
    """Fake browser: each bullet prints 4 lines, 50 lines a page (up to 25 bullets -> 2 pages)."""
    from app import layout
    bullets = [Bullet(text=f"FICTIONAL bullet number {i} about testing APIs.", original="x", changed=False)
               for i in range(30)]
    r = TailoredResume(name="Test Person", headline=None, contact={}, summary="FICTIONAL.", summary_source="composed",
                       skills=[], skill_groups=[], education=[], certifications=[], achievements=[],
                       experience=[Role(company="Example Corp (fictional)", title="QA", start_date="2020-01",
                                        end_date=None, omitted=0, bullets=bullets)])

    def measure(html):
        n = len(r_current["r"].experience[0].bullets)
        return (n * 4.0, -(-n * 4 // 50))
    r_current = {"r": r}

    def render(x):
        r_current["r"] = x
        return ""
    report = layout.fit_measured(r, render, measure=measure)
    assert report["printed_pages"] == 2 and report["fits"]
    assert len(r.experience[0].bullets) in (24, 25)    # the fullest two-page version, not a shorter one


def test_fill_prompt_gives_word_targets_and_tools_not_sentences(profile_data):
    r = resume("Built API tests in Python for the payments service.", ["pytest"])
    llm = FillLLM([])
    linefit.polish(llm, r, profile(profile_data), JOB, lambda x: "", measure=fake_measure(set()))
    prompt = llm.calls[0]
    assert "(9 words; write versions of 14, 12, 6, 4 words)" in prompt
    assert "ALLOWED TOOLS for L1: pytest, Jenkins" in prompt and "FICTIONAL: Ran API tests" not in prompt


def test_joined_bullets_are_rejected():
    orig = "Built API tests in Python with pytest for the payments service."
    assert linefit.check_fill(orig, orig + " Ran them nightly.", "", JOB) is not None
