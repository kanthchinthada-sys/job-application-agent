"""The page estimate against the real thing: Chrome prints the tailored resume, and it must fit 2 pages."""

from contextlib import closing
from io import BytesIO

import pytest
from pypdf import PdfReader

from app import database, layout, repository
from app.resume_render import render
from app.schemas import Profile
from tests.test_layout import big_resume

pytestmark = pytest.mark.e2e


def _pdf_pages(browser, html: str) -> int:
    page = browser.new_page()
    page.set_content(html)
    page.emulate_media(media="print")
    pdf = page.pdf(format="Letter", prefer_css_page_size=True)
    page.close()
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


@pytest.mark.parametrize("shape", [(14, 12, 8, 6), (10, 10, 5, 5), (6, 4, 3, 2)])
def test_fitted_resume_prints_on_two_pages(browser, shape):
    r = big_resume(roles=shape, groups=7)
    report = layout.fit(r, {})
    pages = _pdf_pages(browser, render(r, "Senior SDET (fictional)", "Example (fictional)"))
    if report["fits"]:
        assert pages <= 2                   # when the app says it fits, it really prints on two pages
    else:
        assert pages > 2                    # and when it says it can't (minimums too high), that is true too


def test_estimate_tracks_the_printed_length(browser):
    """The estimate (in estimated lines) stays within 8% of the lines Chrome actually prints."""
    for shape in [(5, 4, 3, 2), (10, 10, 5, 5), (14, 12, 8, 6)]:
        r = big_resume(roles=shape, project=4, groups=5)
        page = browser.new_page()
        page.set_content(render(r, "SDET (fictional)", "Example (fictional)"))
        page.emulate_media(media="print")
        height = page.evaluate("() => document.querySelector('[contenteditable]').getBoundingClientRect().height")
        page.close()
        real_pages = height / (10.5 * 1.25 * 96 / 72) / 54.8          # 54.8 printed lines per Letter page
        assert abs(layout.estimate_pages(r) - real_pages) / real_pages < 0.08, (shape, layout.estimate_pages(r), real_pages)


@pytest.mark.parametrize("shape", [(14, 12, 8, 6), (10, 10, 5, 5)])
def test_measured_fit_prints_on_two_pages_and_keeps_minimums(shape, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_PAGE_MEASURE", "auto")         # unit tests switch the browser off
    r = big_resume(roles=shape, groups=7)
    report = layout.fit_measured(r, lambda x: render(x, "SDET (fictional)", "Example (fictional)"), {})
    assert report["measured"] and report["fits"] and report["printed_pages"] <= 2
    counts = [len(role.bullets) for role in r.experience]
    assert all(c >= m for c, m in zip(counts, layout.MIN_BULLETS))


def test_strict_pdf_keeps_role_minimums_and_blocks_an_old_short_version(client, db_path):
    resume = big_resume(roles=(14, 10, 9, 6), project=5, groups=8)
    resume.summary = ("Built fictional regression tooling to verify service behavior after each release. "
                      "Investigated unexpected API responses using logs, test outputs and deployment evidence. "
                      "Developed repeatable integration checks for distributed workflows and diagnosed regressions "
                      "with developers. Automated evidence collection to make failed checks easier to reproduce and "
                      "worked with delivery teams to prioritize high-risk scenarios. Maintained test data and "
                      "validated environment readiness before release testing.")
    profile = Profile.model_validate({
        "full_name": resume.name,
        "resume_settings": {"strict_layout": True, "min_bullets": [8, 6, 5, 4]},
        "experience": [r.model_dump(include={"company", "title", "start_date", "end_date"}) for r in resume.experience],
    })
    client.put("/profile", json=profile.model_dump(mode="json")).raise_for_status()
    job = client.post("/jobs", json={"title": "Engineer (fictional)", "company": "Example (fictional)",
                                    "description": "Fictional role for integration testing.",
                                    "required_skills": ["Integration testing"]}).raise_for_status().json()
    application = client.post("/applications", json={"job_id": job["id"]}).raise_for_status().json()
    short = resume.model_copy(deep=True)
    short.experience[0].bullets = short.experience[0].bullets[:3]
    report = layout.fit_strict(resume, profile, lambda r: render(r, job["title"], job["company"]))
    assert report["summary_lines"] >= 5 and report["printed_pages"] == 2
    assert all(len(role.bullets) >= floor for role, floor in zip(resume.experience, [8, 6, 5, 4], strict=True))
    assert len(resume.skill_groups) >= 8 and len(resume.projects[0]["highlights"]) <= 5
    with closing(database.connect(db_path)) as conn:
        old = repository.add_document(conn, application["id"], "resume", "", short.model_dump_json(), "agent", None, {})
        good = repository.add_document(conn, application["id"], "resume", "", resume.model_dump_json(), "agent", None, {})
    invalid = client.get(f"/documents/{old.id}/pdf")
    assert invalid.status_code == 409 and "has 3 bullets and needs at least 8" in invalid.json()["detail"]
    exported = client.get(f"/documents/{good.id}/pdf").raise_for_status()
    assert len(PdfReader(BytesIO(exported.content)).pages) == 2
