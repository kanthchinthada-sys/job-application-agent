"""The Greenhouse filler against a FAKE Greenhouse form: fills correctly, never submits, records outcomes."""

import threading
import time

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from app.greenhouse_filler import fill_form, watch_for_submission
from tests.e2e.conftest import _free_port
from tests.e2e.fake_greenhouse import make_app

pytestmark = pytest.mark.e2e


@pytest.fixture
def fake_gh():
    app = make_app()
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield app, f"http://127.0.0.1:{port}/examplecorp/jobs/111"
    srv.should_exit = True
    t.join(timeout=5)


def plan_for(url: str) -> dict:
    """An APPROVED plan, as the app would produce it (fictional answers)."""
    def f(name, ftype, answer="", label=None):
        return {"name": name, "label": label or name, "type": ftype, "answer": answer}
    return {"apply_url": url, "fields": [
        f("first_name", "text", "Jordan"), f("last_name", "text", "Sample"), f("email", "text", "jordan@example.com"),
        f("country", "single_select", "Canada"), f("phone", "text", "+1 555 0100"),
        f("location", "text", "Toronto, ON, Canada"), f("resume", "file"), f("cover_letter", "file"),
        f("question_1", "text", "Company website"), f("question_2", "text", "https://example.com/jordan"),
        f("question_3", "single_select", "No"),
        f("question_4[]", "multi_select", "Acknowledge/Confirm", label="Privacy policy"),
        f("question_5", "textarea", "I build pytest suites for payments APIs."),
        f("gender", "single_select", ""),                                  # demographic: left blank
        f("question_99", "text", "Not on this form"),                     # plan field the form doesn't have
    ]}


@pytest.fixture
def files(tmp_path):
    resume = tmp_path / "Jordan_Sample_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fictional resume")
    letter = tmp_path / "Jordan_Sample_Cover_Letter.pdf"
    letter.write_bytes(b"%PDF-1.4 fictional letter")
    return {"resume": resume, "cover_letter": letter}


@pytest.fixture
def gh_page(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    yield page
    ctx.close()


def test_fills_everything_and_never_submits(gh_page: Page, fake_gh, files):
    app, url = fake_gh
    report = fill_form(gh_page, plan_for(url), files)
    assert report.ok, report.problems
    expect(gh_page.locator("#first_name")).to_have_value("Jordan")
    expect(gh_page.locator("#country")).to_have_value("Canada")
    expect(gh_page.locator("#question_3")).to_have_value("No")
    expect(gh_page.get_by_role("checkbox", name="Acknowledge/Confirm")).to_be_checked()
    expect(gh_page.locator("#question_5")).to_have_value("I build pytest suites for payments APIs.")
    expect(gh_page.locator("#gender")).to_have_value("")                 # demographic stays blank
    expect(gh_page.locator(".filename[data-for=resume]")).to_have_text("Jordan_Sample_Resume.pdf")
    assert "gender" in report.skipped and "question_99" in report.not_found
    time.sleep(1)
    assert app.state.posts == []                                          # NOTHING was submitted


def test_user_submits_and_confirmation_is_recorded(gh_page: Page, fake_gh, files):
    app, url = fake_gh
    fill_form(gh_page, plan_for(url), files)
    gh_page.get_by_role("button", name="Submit application").click()      # the USER clicks submit
    outcome = watch_for_submission(gh_page, timeout_s=20)
    assert outcome.outcome == "confirmed" and "Confirmation page" in outcome.evidence
    assert len(app.state.posts) == 1 and app.state.posts[0]["resume"] == "Jordan_Sample_Resume.pdf"


def test_no_confirmation_is_uncertain(gh_page: Page, fake_gh, files):
    _, url = fake_gh
    fill_form(gh_page, plan_for(url + "?mode=uncertain"), files)
    gh_page.get_by_role("button", name="Submit application").click()
    outcome = watch_for_submission(gh_page, timeout_s=20, confirm_wait_s=2)
    assert outcome.outcome == "uncertain"


def test_validation_error_is_not_a_submission(gh_page: Page, fake_gh, files):
    _, url = fake_gh
    fill_form(gh_page, plan_for(url + "?mode=error"), files)
    gh_page.get_by_role("button", name="Submit application").click()
    outcome = watch_for_submission(gh_page, timeout_s=4, confirm_wait_s=1)
    assert outcome.outcome == "not_submitted"                             # error page: keep waiting, never "confirmed"


def test_rejected_upload_is_reported(gh_page: Page, fake_gh, files):
    _, url = fake_gh
    report = fill_form(gh_page, plan_for(url + "?mode=badupload"), files)
    assert not report.ok and any("Jordan_Sample_Resume.pdf" in p for p in report.problems)


def test_closing_the_browser_without_submitting(browser, fake_gh, files):
    _, url = fake_gh
    ctx = browser.new_context()
    page = ctx.new_page()
    fill_form(page, plan_for(url), files)
    page.close()
    assert watch_for_submission(page, timeout_s=5).outcome == "not_submitted"
    ctx.close()
