"""Fictional portal pages served entirely by Playwright interception."""

import pytest

from app import career_browser

pytestmark = pytest.mark.e2e

DOCUMENTS = {"resume": {"name": "Fictional-Resume-v1.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-fictional"}}
CONTACT = {"full_name": "Jordan Sample", "email": "jordan.sample@example.com"}

FORM = """
<h1>Fictional Test Engineer</h1><form id="application">
  <label>Full name<input name="full_name" required></label>
  <label>Email<input name="email" type="email" required></label>
  <label>Resume / CV<input type="file" name="resume" required></label>
  {extra}
  <button type="submit">Submit application</button>
</form><script>
  window.sent = 0;
  document.querySelector('form').addEventListener('submit', event => {{
    event.preventDefault();
    window.sent += 1;
    window.received = [...new FormData(event.target).entries()].map(([key, value]) => [key, value.name || value]);
    document.body.innerHTML = '<h1>Thank you for applying</h1>';
  }});
</script>"""


@pytest.mark.parametrize("url", ["https://boards.greenhouse.io/fictional/jobs/111",
                                "https://jobs.lever.co/fictional/role1",
                                "https://jobs.ashbyhq.com/fictional/role2"])
def test_portals_fill_attach_and_confirm_once(page, url):
    target = career_browser.target_for(url)
    page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=FORM.format(extra="")))
    page.goto(target.url)
    attempts = []
    result = career_browser.run_form(page, target, CONTACT, {}, DOCUMENTS, set(), False, lambda: attempts.append(True))
    assert result.state == "confirmed" and result.uploaded == {"resume"}
    assert attempts == [True]
    assert dict(page.evaluate("window.received")) == {"full_name": CONTACT["full_name"], "email": CONTACT["email"],
                                                      "resume": DOCUMENTS["resume"]["name"]}
    again = career_browser.run_form(page, target, CONTACT, {}, DOCUMENTS, set(), False, lambda: attempts.append(True))
    assert again.state == "already_applied" and attempts == [True] and page.evaluate("window.sent") == 1


def test_unknown_required_and_consent_pause_and_resume_without_guessing(page):
    target = career_browser.target_for("https://jobs.lever.co/fictional/role")
    extra = '<label>Sponsorship required?<select name="sponsor" required><option value="">Choose</option>' \
            '<option value="yes">Yes</option><option value="no">No</option></select></label>' \
            '<label><input type="checkbox" name="privacy" required>I agree to the Privacy Policy</label>'
    page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=FORM.format(extra=extra)))
    page.goto(target.url)
    attempts, uploaded = [], set()

    def run(answers=None, reviewed=False):
        return career_browser.run_form(page, target, CONTACT, answers or {}, DOCUMENTS, uploaded, reviewed,
                                       lambda: attempts.append(True))

    first = run()
    assert first.state == "needs_you"
    sponsorship, consent = first.questions
    assert sponsorship.options == ["Yes", "No"] and consent.manual
    assert page.locator('[name="sponsor"]').input_value() == "" and not page.locator('[name="privacy"]').is_checked()
    assert run({sponsorship.key: "No", consent.key: "I agree to the Privacy Policy"}).state == "needs_you"
    assert page.locator('[name="sponsor"]').input_value() == "no" and not page.locator('[name="privacy"]').is_checked()
    page.get_by_role("checkbox").check()
    assert run({sponsorship.key: "No"}, reviewed=True).state == "confirmed"
    assert attempts == [True]


def test_workday_login_pause_and_multistep_review(page):
    target = career_browser.target_for("https://fictional.wd1.myworkdayjobs.com/en-US/Jobs/job/City/Test_R111")
    login = """<h1>Sign In</h1><input type='password' placeholder='Password'><button>Sign in</button>
    <script>document.querySelector('button').onclick = () => { document.body.innerHTML = `
    <h1>My Information</h1><form><label>First name<input name='firstName' required></label>
    <label>Resume<input type='file' name='resume' required></label><button type='button'>Save and Continue</button></form>`;
    document.querySelector('button').onclick = () => { window.nameAtReview = document.querySelector('input').value;
    document.body.innerHTML = '<h1>Review</h1><button>Submit</button>';
    document.querySelector('button').onclick = () => { document.body.innerHTML = '<h1>Your application was submitted</h1>'; };
    }; };</script>"""
    page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=login))
    page.goto(target.url)
    uploads, attempts = set(), []

    def run(reviewed=False):
        return career_browser.run_form(page, target, {"first_name": "Jordan"}, {}, DOCUMENTS, uploads, reviewed,
                                       lambda: attempts.append(True))

    assert "sign-in" in run().message and not attempts
    page.get_by_role("button", name="Sign in").click()
    assert "final page" in run().message and "resume" in uploads and not attempts
    assert page.evaluate("window.nameAtReview") == "Jordan"
    assert run(reviewed=True).state == "confirmed" and attempts == [True]


def test_redirect_to_a_different_posting_leaves_the_form_untouched(page):
    target = career_browser.target_for("https://jobs.ashbyhq.com/fictional/abc")
    page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=FORM.format(extra="")))
    page.goto("https://jobs.ashbyhq.com/fictional/other/application")
    attempts = []
    result = career_browser.run_form(page, target, CONTACT, {}, DOCUMENTS, set(), False, lambda: attempts.append(True))
    assert result.state == "needs_you" and not attempts
    assert page.get_by_label("Full name").input_value() == ""


def test_submit_button_that_accepts_terms_stays_manual(page):
    target = career_browser.target_for("https://jobs.ashbyhq.com/fictional/terms")
    html = FORM.format(extra='<p>By clicking “Submit application” you agree to the Terms and Conditions.</p>')
    page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto(target.url)
    attempts = []
    result = career_browser.run_form(page, target, CONTACT, {}, DOCUMENTS, set(), False, lambda: attempts.append(True))
    assert result.state == "needs_you" and "accepts terms" in result.message and not attempts
