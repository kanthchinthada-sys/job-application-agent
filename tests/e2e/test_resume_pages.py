"""The page estimate against the real thing: Chrome prints the tailored resume, and it must fit 2 pages."""

import pytest

from app import layout
from app.resume_render import render
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
