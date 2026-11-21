"""ATS check: alternatives, other word forms, and experience-bank evidence (FICTIONAL data)."""
from app import ats
from app.tailoring import check_rewording
from app.schemas import JobOut
from datetime import datetime


def test_alternatives_and_word_forms_count_as_present():
    text = "Built automation frameworks in Java. Release quality gate checks. Python scripts."
    assert ats.resume_has(text, "Python/Java/Scala/go")
    assert ats.resume_has("Built automated test framework in Java", "Automated test frameworks")
    assert ats.resume_has(text, "Quality gates")
    assert not ats.resume_has(text, "Kubernetes")
    assert ats._alternatives("CI/CD") == [] and ats._alternatives("CI/CD pipelines") == []


def test_bank_records_evidence_keywords(profile_data):
    from app.schemas import Profile
    profile_data["experience_bank"] = [{"id": "XX01", "text": "FICTIONAL: Participated in Agile planning and retros.",
                                        "owner": profile_data["experience"][0]["company"], "owner_kind": "experience",
                                        "status": "verified"}]
    p = Profile(**profile_data)
    rep = ats.analyze("pytest", "", ["Agile methodologies"], [], set(), p)
    assert [k.keyword for k in rep.missing_but_evidenced] == ["Agile methodologies"]
    assert "Agile" in rep.missing_but_evidenced[0].evidence


def test_rewording_may_use_generic_posting_words():
    job = JobOut(id=1, title="SDET", company="X", description="FICTIONAL. Build test frameworks across systems; "
                 "testing at scale.", required_skills=["Selenium"], preferred_skills=[], created_at=datetime.now())
    original = "Developed a Selenium automation framework with reusable components."
    new = "Developed Selenium automation frameworks with reusable components for testing across systems."
    assert check_rewording(original, new, job) is None


def test_printed_pages_leave_no_room_for_browser_header_footer():
    """Browsers print date/title/URL in the page margin; with no page margin there is nowhere to print them."""
    from app.resume_render import render_letter
    html = render_letter("FICTIONAL letter.", "Test Person", {}, "SDET", "Example Corp")
    assert "@page { size: Letter; margin: 0; }" in html
    assert "<thead><tr><td><div class='space'>" in html     # margins recreated inside every page


def test_resume_uses_smart_justification_without_hyphenation():
    from app.resume_render import render_letter
    html = render_letter("FICTIONAL letter.", "Test Person", {}, "SDET", "Example Corp")
    assert 'class="smart-justify"' in html and "window.smartJustify" in html
    assert "hyphens: auto" not in html     # hyphenation would split keywords for ATS readers


def test_a_shorter_different_word_is_not_a_match():
    from app.ats import resume_has
    assert not resume_has("Validated cross-tenant access restrictions (FICTIONAL).", "accessibility testing")
    assert resume_has("Built automated test frameworks (FICTIONAL).", "automation frameworks")


def test_quality_reporting_counts_as_quality_metrics():
    from app.ats import find_evidence
    assert find_evidence("Established reporting for defect escape trends and regression stability (FICTIONAL).",
                         "quality metrics")
