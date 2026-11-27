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


def test_lever_cards_show_real_screening_questions_and_pause_for_private_answers(page):
    target = career_browser.target_for("https://jobs.lever.co/fictional/lever-cards")
    extra = """
    <label>Current company<input name="org" required></label>
    <div class="custom-question"><div class="application-label">Would you require sponsorship?✱</div>
      <select name="cards[fictional][field0]" required><option value="">Select</option>
        <option value="y">Yes</option><option value="n">No</option></select></div>
    <div class="custom-question"><div class="application-label">Have you been convicted of a crime?✱</div>
      <select name="cards[fictional][field1]" required><option value="">Select</option>
        <option value="y">Yes</option><option value="n">No</option></select></div>
    <div class="custom-question"><div class="application-label">Share an example of your experience with API testing.</div>
      <textarea name="cards[fictional][field2]"></textarea></div>
    """
    page.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=FORM.format(extra=extra)))
    page.goto(target.url)
    attempts, uploaded = [], set()
    contact = {**CONTACT, "current_company": "Fictional Tools"}

    def run(answers, reviewed=False):
        return career_browser.run_form(page, target, contact, answers, DOCUMENTS, uploaded, reviewed, lambda: attempts.append(True))

    paused = run({})
    sponsor, criminal, example = paused.questions
    assert paused.state == "needs_you" and not attempts
    assert sponsor.label.startswith("Would you require sponsorship") and sponsor.options == ["Yes", "No"]
    assert criminal.label.startswith("Have you been convicted") and criminal.manual
    assert example.draftable and not example.required
    editable = {sponsor.key: "No", example.key: "I verified integration failures against API responses and logs."}
    assert run({**editable, criminal.key: "No"}).state == "needs_you"
    assert page.locator('select[name="cards[fictional][field1]"]').input_value() == ""
    page.locator('select[name="cards[fictional][field1]"]').select_option(label="No")
    assert run(editable, reviewed=True).state == "confirmed" and attempts == [True]
    assert dict(page.evaluate("window.received"))["org"] == contact["current_company"]


def test_greenhouse_dynamic_selects_and_hidden_cover_letter(page):
    target = career_browser.target_for("https://job-boards.greenhouse.io/fictional/jobs/222")
    html = """
    <div role="listbox" hidden><div role="option">Canada+1</div><div role="option">Yes</div></div>
    <form>
      <label for="full">What is your legal name?*</label><input required id="full">
      <label for="linkedin">LinkedIn Profile</label><input id="linkedin" type="url">
      <label for="why">Why are you interested in this role?</label><input id="why">
      <div id="widgets"></div>
      <div class="file-upload" role="group" aria-labelledby="upload-label-resume"><div id="upload-label-resume">Resume/CV</div>
        <div hidden><label for="resume">Attach</label><input id="resume" type="file"></div></div>
      <div class="file-upload" role="group" aria-labelledby="upload-label-cover_letter"><div id="upload-label-cover_letter">Cover Letter</div>
        <div hidden><label for="cover_letter">Attach</label><input id="cover_letter" type="file"></div></div>
      <button>Submit application</button>
    </form>
    <script>
      window.selectedChoices = {};
      const specs = [
        ['country', 'Country*', ['Canada +1', 'United States +1']],
        ['candidate-location', 'Location (City)*', ['Example City, Ontario, Canada', 'Example City, Texas, United States']],
        ['question_auth', 'Are you legally authorized to work in Canada?*', ['Yes', 'No']],
        ['question_clearance[]', 'What is your current Canadian government security clearance status?*',
           ['Reliability Status', 'Secret', 'No clearance / Ineligible']]
      ];
      for (const [id, label, options] of specs) {
        const root = document.createElement('div');
        const inputId = 'react-select-' + id + '-listbox';
        root.innerHTML = `<label id="${id}-label" for="${id}">${label}</label><div class="select__control">
          <input id="${id}" role="combobox" aria-required="true" aria-expanded="false" aria-labelledby="${id}-label" /></div>`;
        document.querySelector('#widgets').append(root);
        const input = root.querySelector('input');
        const close = () => { root.querySelector('[role=listbox]')?.remove(); input.setAttribute('aria-expanded','false'); input.removeAttribute('aria-controls'); };
        const show = () => {
          close();
          const listbox = document.createElement('div'); listbox.id = inputId; listbox.setAttribute('role','listbox');
          input.setAttribute('aria-expanded','true'); input.setAttribute('aria-controls', inputId);
          for (const option of options.filter(x => x.toLowerCase().includes(input.value.toLowerCase()))) {
            const row = document.createElement('div'); row.setAttribute('role','option'); row.innerText = option;
            if (window.selectedChoices[id] === option) row.className = 'select__option--is-selected';
            row.onclick = () => {
              const selected = document.createElement('span');
              selected.className = id.endsWith('[]') ? 'select__multi-value__label' : 'select__single-value';
              selected.innerText = id === 'country' ? '+1' : option;
              root.querySelector('.' + selected.className)?.remove(); input.parentElement.prepend(selected);
              input.value = ''; window.selectedChoices[id] = option; close();
            };
            listbox.append(row);
          }
          root.append(listbox);
        };
        input.onclick = show; input.oninput = show;
        input.onkeydown = event => { if (event.key === 'Escape') close(); };
      }
      document.querySelector('form').onsubmit = event => {
        event.preventDefault(); window.files = ['resume','cover_letter'].map(id => document.getElementById(id).files[0]?.name);
        window.written = document.querySelector('#why').value; document.body.innerHTML = '<h1>Application submitted</h1>';
      };
    </script>
    """
    page.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html))
    page.goto(target.url)
    contact = {**CONTACT, "linkedin": "https://www.linkedin.com/in/fictional/", "country": "Canada", "location": "Example City, ON, Canada"}
    files = {**DOCUMENTS, "cover_letter": {"name": "Fictional-Cover-Letter.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-fictional"}}
    answers = {career_browser.saved_answer_key("question_auth", "Are you legally authorized to work in Canada?"): "Yes",
               career_browser.saved_answer_key("why", "Why are you interested in this role?"): "I enjoy building regression tooling for distributed systems."}
    uploads, attempts = set(), []
    def run(reviewed=False):
        return career_browser.run_form(page, target, contact, answers, files, uploads, reviewed, lambda: attempts.append(True))

    paused = run()
    assert paused.state == "needs_you" and not attempts and uploads == {"resume", "cover_letter"}
    assert page.get_by_label("What is your legal name?*").input_value() == CONTACT["full_name"]
    assert page.get_by_label("LinkedIn Profile").input_value() == contact["linkedin"]
    clearance, = paused.questions
    assert clearance.options == ["Reliability Status", "Secret", "No clearance / Ineligible"]
    assert clearance.key == career_browser.saved_answer_key("question_clearance[]", clearance.label)
    auth_control = next(f for f in page.evaluate(career_browser._SCAN) if f["name"] == "question_auth")
    answers[career_browser._field_key([auth_control])] = "No"  # An answer persisted by an older paused run.
    answers[clearance.key] = "No clearance / Ineligible"
    auth_key = career_browser.saved_answer_key("question_auth", auth_control["label"])
    answers[auth_key] = "Employer removed this option"
    assert run(reviewed=True).state == "needs_you" and not attempts
    answers[auth_key] = "Yes"
    assert run().state == "confirmed" and attempts == [True]
    assert page.evaluate("window.selectedChoices") == {"country": "Canada +1", "candidate-location": "Example City, Ontario, Canada",
                                                       "question_auth": "Yes", "question_clearance[]": "No clearance / Ineligible"}
    assert page.evaluate("window.files") == [DOCUMENTS["resume"]["name"], files["cover_letter"]["name"]]
    assert page.evaluate("window.written") == answers[career_browser.saved_answer_key("why", "Why are you interested in this role?")]
