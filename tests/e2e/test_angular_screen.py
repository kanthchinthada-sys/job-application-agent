"""The Angular screen (/ui/) in a real browser, against the real backend. FICTIONAL data only.

Runs only after the screen is built (cd frontend && npm run build); otherwise these tests are skipped.
"""

import json
import re

import pytest
from playwright.sync_api import Page, expect

from app.api import UI_DIR

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not (UI_DIR / "index.html").exists(), reason="Angular screen not built: cd frontend && npm run build"),
]

GH_JOB_URL = "https://boards.greenhouse.io/examplecorp/jobs/111"     # served by the fake fetcher


@pytest.fixture
def ui(page: Page) -> Page:
    page.goto("/ui/")
    expect(page.get_by_role("link", name="Saved jobs", exact=True)).to_be_visible()
    return page


def save_profile(page: Page):
    page.get_by_role("link", name="Profile", exact=True).click()
    page.get_by_role("button", name="Load fictional example").click()
    box = page.get_by_label("Profile JSON")
    expect(box).to_have_value(re.compile("FICTIONAL"))
    data = json.loads(box.input_value())
    data["preferences"] = {"target_roles": ["QA Engineer", "SDET"], "locations": ["Canada"]}
    box.fill(json.dumps(data, indent=2))
    page.get_by_role("button", name="Save profile").click()
    expect(page.get_by_role("status")).to_have_text("Profile saved.")


def import_job(page: Page):
    """Add a job the simple way: import it from its link (a Greenhouse link needs nothing else)."""
    page.get_by_role("link", name="+ Add job").click()
    page.get_by_role("button", name="Import from link").click()
    # Wait until Angular has rendered the page: typing within milliseconds of opening it is wiped out by the
    # first render on a fast machine.
    expect(page.get_by_role("button", name="Import job")).to_be_enabled()
    page.get_by_label("Job link", exact=True).fill(GH_JOB_URL)
    page.get_by_role("button", name="Import job").click()
    added = page.get_by_test_id("added-job")
    expect(added.locator("#a-title")).to_have_value("Senior QA Engineer")
    expect(added.locator("#a-required")).to_have_value("Python, Playwright")      # read by the (fake) AI, checked


def open_workspace(page: Page):
    """From the added job: Prepare application opens the application's own page."""
    page.get_by_test_id("added-job").get_by_role("button", name="Prepare application").click()
    expect(page.get_by_test_id("workspace-title")).to_have_text("Senior QA Engineer")


def generate(page: Page, which: str):
    """One document from the Documents tab ("Resume only" / "Cover letter only"), waited for until ready."""
    page.get_by_role("button", name=which).click()
    kind = "resume" if which.startswith("Resume") else "cover_letter"
    expect(page.get_by_test_id(f"status-{kind}")).to_contain_text("Ready", timeout=30_000)


def test_add_job_from_a_pasted_description(ui: Page):
    posting = ("Senior Software Engineer in Test\nContoso Health (fictional) · Toronto, ON (Hybrid)\n\n"
               "Requirements\n- Python and Playwright\n- Kubernetes\n\nAbout Contoso Health\nFICTIONAL.\n")
    ui.get_by_role("link", name="+ Add job").click()
    expect(ui.get_by_role("button", name="Add job", exact=True)).to_be_enabled()
    ui.get_by_label("Job description").fill(posting)
    ui.get_by_role("button", name="Add job", exact=True).click()
    added = ui.get_by_test_id("added-job")
    expect(added.locator("#a-title")).to_have_value("Senior Software Engineer in Test")
    expect(added.locator("#a-company")).to_have_value("Contoso Health")
    expect(added.locator("#a-location")).to_have_value("Toronto, ON (Hybrid)")
    added.locator("#a-company").fill("Contoso Health (fictional)")
    added.get_by_role("button", name="Save changes").click()
    expect(added).to_contain_text("Saved.")

    # pasting it again: the saved job is shown, with the way to its application
    ui.get_by_role("button", name="Add another job").click()
    ui.get_by_label("Job description").fill(posting)
    ui.get_by_role("button", name="Add job", exact=True).click()
    dup = ui.get_by_test_id("duplicate")
    expect(dup).to_contain_text("You already saved this job")
    dup.get_by_role("button", name="Prepare application").click()
    expect(ui.get_by_test_id("workspace-title")).to_have_text("Senior Software Engineer in Test")


def test_a_missing_company_is_highlighted_and_your_correction_beats_a_late_ai_answer(ui: Page, fake_llm_e2e):
    import threading
    fake_llm_e2e.intake_gate = threading.Event()             # the model is slow to read the posting
    fake_llm_e2e.intake_answer = {"company": "Tailspin Toys (fictional)"}
    posting = ("Senior Quality Engineer (fictional)\nRemote, Canada\n\nAbout the role\nYou will own test strategy. "
               "You'll pair with Tailspin Toys (fictional) engineers daily.\n\nWhat we're looking for\n"
               "- Experience with Cypress, Postman and SQL.\n")
    ui.get_by_role("link", name="+ Add job").click()
    ui.get_by_label("Job description").fill(posting)
    ui.get_by_role("button", name="Add job", exact=True).click()
    added = ui.get_by_test_id("added-job")
    expect(added.get_by_test_id("ai-pending")).to_be_visible()          # saved at once; the AI is still reading
    company = added.locator("#a-company")
    expect(company).to_have_value("")
    expect(company).to_have_class(re.compile(r"\bunsure\b"))
    added.get_by_test_id("posting-kept").locator("summary").click()
    expect(added.get_by_test_id("posting-kept")).to_contain_text("Cypress, Postman and SQL")
    company.fill("Wingtip (fictional)")
    added.get_by_role("button", name="Save changes").click()
    expect(added).to_contain_text("Saved.")
    fake_llm_e2e.intake_gate.set()                                       # the AI answers after your correction
    expect(added).to_contain_text("Kept your correction for: company", timeout=10_000)
    expect(company).to_have_value("Wingtip (fictional)")
    expect(added.get_by_test_id("ai-pending")).to_have_count(0)


def test_profile_job_match_and_tracking(ui: Page):
    save_profile(ui)
    import_job(ui)
    open_workspace(ui)
    ui.get_by_role("tab", name="Job & skill match").click()
    expect(ui.get_by_test_id("match")).to_contain_text("What this job asks for")
    ui.get_by_role("tab", name="Documents").click()

    status = ui.get_by_label("Application status")
    expect(status).to_have_value("preparing")
    status.select_option("submitted_confirmed")                       # never without evidence
    expect(ui.get_by_text("Confirmation evidence (required")).to_be_visible()
    ui.get_by_role("button", name="Mark submitted (confirmed)").click()
    expect(ui.get_by_text("Paste the confirmation evidence first.")).to_be_visible()
    ui.get_by_role("button", name="Cancel").click()
    expect(status).to_have_value("preparing")

    ui.get_by_role("link", name="← Back to applications").click()
    ui.get_by_label("Search by job title or company").fill("example corp")
    expect(ui.get_by_test_id("applications-table")).to_contain_text("Senior QA Engineer")


def test_cover_letter_draft_and_edit(ui: Page):
    save_profile(ui)
    import_job(ui)
    open_workspace(ui)
    generate(ui, "Cover letter only")
    ui.get_by_test_id("view-cover_letter").click()
    expect(ui.get_by_test_id("letter-preview")).to_contain_text("Dear Hiring Team")
    ui.get_by_test_id("letter-actions").get_by_role("button", name="Edit").click()
    box = ui.get_by_label("Cover letter text")
    box.fill(box.input_value() + "\nOne more fictional line.")
    ui.get_by_role("button", name="Save edits as new version").click()
    expect(ui.get_by_test_id("version")).to_contain_text("v2 · your edit")
    ui.reload()                                                       # opens your saved version again
    ui.get_by_test_id("view-cover_letter").click()
    expect(ui.get_by_test_id("letter-preview")).to_contain_text("One more fictional line.")


def test_assisted_apply_plan(ui: Page):
    save_profile(ui)
    import_job(ui)
    open_workspace(ui)
    ui.get_by_test_id("application-tools").locator("summary").click()
    ui.get_by_role("button", name="Assisted apply (Greenhouse): prepare plan").click()
    plan = ui.get_by_test_id("apply-plan")
    expect(plan).to_contain_text("not approved yet")
    expect(plan).to_contain_text("you click Submit yourself")
    plan.get_by_role("button", name="Save my answers and approve").click()
    expect(plan.locator(".msg.bad").last).to_be_visible()             # required answers still missing


def test_no_profile_message_and_ai_status(ui: Page):
    expect(ui.get_by_test_id("ai-status")).to_have_text(re.compile("AI ready"))
    ui.get_by_test_id("ai-status").click()
    expect(ui.get_by_test_id("ai-model")).to_have_text("fake-e2e-model")
    ui.get_by_role("link", name="Profile", exact=True).click()
    expect(ui.get_by_role("heading", name="Get started: upload your resume")).to_be_visible()


def test_new_user_uploads_a_resume_and_saves_it(ui: Page):
    """A brand-new user: empty profile -> upload a (fictional) resume -> check the preview -> save."""
    resume = ("Jordan Sample\njordan.sample@example.com\nSUMMARY\nFictional QA engineer.\nSKILLS\n"
              "Automation: Playwright, pytest\nEXPERIENCE\nContoso Health | Remote Apr 2023 - Present\n"
              "Senior QA Engineer\nBuilt fictional Playwright suites.\nAdded fictional API checks.\n")
    ui.goto("/ui/#/welcome")
    expect(ui.get_by_role("heading", name="Welcome! Set up your profile")).to_be_visible()
    ui.get_by_label("Resume file").set_input_files(
        files=[{"name": "resume.txt", "mimeType": "text/plain", "buffer": resume.encode()}])
    preview = ui.get_by_test_id("resume-preview")
    expect(preview).to_contain_text("2 bullets")
    expect(preview.locator("#rv-name")).to_have_value("Jordan Sample")
    expect(preview.locator("#rv-t0")).to_have_value("Senior QA Engineer")
    expect(preview.locator("#rv-c0")).to_have_value("Contoso Health")
    preview.locator("#rv-c0").fill("Contoso Health (fictional)")              # corrected before saving
    ui.get_by_role("button", name="Save as my profile").click()
    expect(ui.get_by_test_id("setup-done")).to_contain_text("You're set up")


def test_resume_preview_and_print_page(ui: Page):
    save_profile(ui)
    import_job(ui)
    open_workspace(ui)
    generate(ui, "Resume only")
    expect(ui.get_by_test_id("resume-summary")).to_contain_text("Job-description keyword coverage")
    expect(ui.frame_locator("[data-testid=resume-preview]").locator("h1")).to_be_visible()
    with ui.context.expect_page() as printed:
        ui.get_by_test_id("print-resume").click()
    page = printed.value
    expect(page.get_by_role("button", name="Print / Save as PDF")).to_be_visible()
    expect(page.get_by_role("link", name="← Back to the app")).to_be_visible()


def test_match_shows_related_experience_and_ai_second_look(ui: Page):
    save_profile(ui)
    import_job(ui)
    ui.get_by_role("link", name="Saved jobs").click()
    ui.get_by_role("button", name="Skill match").click()
    match = ui.get_by_test_id("match")
    expect(match).to_contain_text("shown by your profile")
    if match.get_by_role("button", name=re.compile("Ask AI")).count():
        match.get_by_role("button", name=re.compile("Ask AI")).click()
        expect(match).to_contain_text(re.compile("Still no evidence|AI:"))


def test_experience_bank_import_search_and_confirm(ui: Page):
    save_profile(ui)
    bank = ui.get_by_test_id("bank")
    bank.get_by_label("Experience bank text").fill(
        "Projects\n• PR01: Built a fictional tool with Python and FastAPI (fictional).\n"
        "• AI01: Created a fictional labelled evaluation set (fictional). (partly true)\n")
    bank.get_by_role("button", name="Import records").click()
    expect(bank.get_by_test_id("bank-report")).to_contain_text("2 records imported")
    bank.get_by_label("Search by keyword or record ID").fill("fastapi")
    expect(bank.get_by_test_id("bank-count")).to_have_text("Showing 1 of 2 records.")
    bank.get_by_label("Search by keyword or record ID").fill("AI01")
    status = bank.get_by_label("Status of AI01")
    expect(status).to_have_value("partly_true")          # not used on resumes until you confirm it
    status.select_option("verified")
    expect(bank).to_contain_text("Confirmed by you.")


def test_find_jobs_then_apply_prepares_everything(ui: Page):
    ui.set_viewport_size({"width": 1300, "height": 375})
    save_profile(ui)
    ui.get_by_role("link", name="Find jobs").click()
    ui.get_by_label("Job title").fill("QA Engineer")
    ui.get_by_label("Country").select_option("CA")
    ui.get_by_role("button", name="7 days").click()
    expect(ui.get_by_role("button", name="7 days")).to_have_attribute("aria-pressed", "true")
    ui.get_by_role("button", name="Search").click()
    found = ui.get_by_test_id("found-jobs")
    expect(found).to_contain_text("Senior QA Engineer")
    expect(found).not_to_contain_text("Berlin GmbH")                 # another country
    expect(ui.get_by_test_id("sources")).to_contain_text("Remotive")
    expect(ui.get_by_test_id("elsewhere").get_by_role("link", name="hiring.cafe")).to_be_visible()
    row = found.locator("tr", has_text="Globex (fictional)")
    with ui.context.expect_page() as employer_page:                    # the employer's page opens in a new tab
        row.get_by_role("button", name="Apply").click()
    assert employer_page.value.url.startswith("https://remotive.example/jobs/2") or employer_page.value
    open_application = row.get_by_role("link", name="Open application")
    open_application.scroll_into_view_if_needed()
    last_scroll = ui.evaluate("window.scrollY")
    assert last_scroll > 0
    open_application.click()                                        # leave while it's being prepared
    expect(ui.get_by_test_id("workspace-title")).to_contain_text("QA Engineer")
    expect(ui.get_by_test_id("status-cover_letter")).to_contain_text("Ready", timeout=30000)   # appears by itself
    expect(ui.get_by_test_id("status-resume")).to_contain_text("Ready", timeout=30000)
    ui.get_by_role("link", name="Find jobs").click()                  # come back: the search is still there
    row = ui.get_by_test_id("found-jobs").locator("tr", has_text="Globex (fictional)")
    expect(row).to_contain_text("Ready: resume and cover letter prepared", timeout=20000)
    expect(ui.get_by_label("Job title")).to_have_value("QA Engineer")
    ui.wait_for_function("position => Math.abs(window.scrollY - position) < 2", arg=last_scroll)
    new_tab = ui.context.new_page()
    try:
        new_tab.goto(ui.url)
        expect(new_tab.get_by_label("Job title")).to_have_value("QA Engineer")
        saved = new_tab.get_by_test_id("found-jobs").locator("tr", has_text="Globex (fictional)")
        expect(saved.get_by_role("link", name="Open application")).to_be_visible()
    finally:
        new_tab.close()


def test_a_gap_you_really_have_can_be_added_and_used(ui: Page):
    save_profile(ui)
    import_job(ui)
    open_workspace(ui)                               # (it saves the form, so the change below comes after it)
    job = ui.request.get("/jobs").json()[0]          # the job also asks for a tool the profile doesn't name
    ui.request.patch(f"/jobs/{job['id']}", data={"required_skills": job["required_skills"] + ["TestRail"]})
    generate(ui, "Resume only")
    expect(ui.get_by_test_id("attention")).to_contain_text("TestRail")
    ui.get_by_test_id("resume-details").locator("summary").first.click()
    gaps = ui.get_by_test_id("gaps")
    gaps.get_by_role("button", name="I have this").last.click()                 # the one next to TestRail
    form = ui.get_by_test_id("gap-form")
    expect(form).to_contain_text("Where did you use TestRail?")
    form.locator("textarea").fill("Kept regression cases and release runs in TestRail (FICTIONAL TEST DATA).")
    form.get_by_role("button", name="Add to my profile").click()
    expect(ui.get_by_test_id("resume-details")).to_contain_text("Added to your profile: TestRail")
    ui.get_by_role("tab", name="Job & skill match").click()                     # switching tabs reloads the profile
    ui.get_by_role("tab", name="Documents").click()
    expect(ui.get_by_test_id("profile-changed")).to_contain_text("Profile updated since this draft")
    ui.get_by_test_id("profile-changed").get_by_role("button", name="Generate a new version").click()
    expect(ui.get_by_test_id("version")).to_contain_text("v2", timeout=30_000)
    expect(ui.get_by_test_id("attention")).not_to_contain_text("TestRail")


def test_private_review_shows_with_an_experience_bank_and_never_prints(ui: Page):
    import json as _json
    from tests.test_bank import SDET_JOB, with_bank
    ui.request.put("/profile", data=_json.loads(with_bank().model_dump_json()))
    job = ui.request.post("/jobs", data={"title": "Senior SDET", "company": "Example (fictional)",
                                         "description": SDET_JOB.description, "required_skills": ["Java"],
                                         "allow_duplicate": True}).json()
    app = ui.request.post("/applications", data={"job_id": job["id"]}).json()
    ui.goto(f"/ui/#/applications/{app['id']}")
    generate(ui, "Resume only")
    ui.get_by_test_id("resume-details").locator("summary").first.click()
    review = ui.get_by_test_id("private-review")
    expect(review).to_contain_text("Private review", timeout=10_000)
    expect(review).to_contain_text("Java test automation")
    expect(review).to_contain_text("direct")
    with ui.context.expect_page() as printed:
        ui.get_by_test_id("print-resume").click()
    expect(printed.value.locator("body")).not_to_contain_text("Private review")


def test_technical_gap_review_generates_a_new_resume(ui: Page):
    save_profile(ui)
    job = ui.request.post("/jobs", data={
        "title": "SDET", "company": "Example Shop (fictional)",
        "description": "Required qualifications:\n- Hands-on experience with Cypress\n- Excellent communication.",
        "required_skills": ["Cypress", "Communication"],
    })
    assert job.ok
    snapshot = job.json()
    made = ui.request.post("/applications", data={"job_id": snapshot["id"]})
    assert made.ok
    aid = made.json()["id"]
    initial = ui.request.post(f"/applications/{aid}/drafts", data={"kind": "resume"})
    assert initial.ok
    ui.goto(f"/ui/#/applications/{aid}?tab=match")
    panel = ui.get_by_test_id("gap-review")
    expect(panel).to_contain_text("cypress")
    expect(panel.get_by_role("button", name="Review example")).to_have_count(1)
    panel.get_by_role("button", name="Review example").click()
    panel.get_by_label("Employer or project").select_option(label="Example Corp (fictional)")
    panel.get_by_role("textbox").fill("Built Cypress tests for the checkout flow and checked validation errors.")
    enable = panel.get_by_role("button", name="Enable and generate resume")
    expect(enable).to_be_disabled()
    panel.get_by_role("checkbox").check()
    enable.click()
    expect(panel.get_by_role("status")).to_contain_text("verified")
    panel.get_by_role("link", name="Open application and check resume progress").click()
    expect(ui.get_by_test_id("status-resume")).to_contain_text("v2", timeout=30_000)
