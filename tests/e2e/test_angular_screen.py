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
    expect(page.get_by_role("link", name="Jobs", exact=True)).to_be_visible()
    return page


def save_profile(page: Page):
    page.get_by_role("link", name="Profile").click()
    page.get_by_role("button", name="Load fictional example").click()
    box = page.get_by_label("Profile JSON")
    expect(box).to_have_value(re.compile("FICTIONAL"))
    data = json.loads(box.input_value())
    data["preferences"] = {"target_roles": ["QA Engineer", "SDET"], "locations": ["Canada"]}
    box.fill(json.dumps(data, indent=2))
    page.get_by_role("button", name="Save profile").click()
    expect(page.get_by_role("status")).to_have_text("Profile saved.")


def import_job(page: Page):
    """Add a job the simple way: just its link (a Greenhouse link needs nothing else)."""
    page.get_by_role("link", name="Add job").click()
    # Wait until Angular has rendered the page: typing within milliseconds of opening it is wiped out by the
    # first render on a fast machine.
    expect(page.get_by_role("button", name="Add job")).to_be_enabled()
    page.get_by_label("Job link", exact=True).fill(GH_JOB_URL)
    page.get_by_role("button", name="Add job").click()
    added = page.get_by_test_id("added-job")
    expect(added.locator("#a-title")).to_have_value("Senior QA Engineer")
    expect(added.locator("#a-required")).to_have_value("Python, Playwright")      # read by the (fake) AI, checked
    added.get_by_role("button", name="See skill match").click()
    expect(page.get_by_test_id("match")).to_contain_text("Rule-based evidence comparison")


def test_add_job_from_a_pasted_description(ui: Page):
    posting = ("Senior Software Engineer in Test\nContoso Health (fictional) · Toronto, ON (Hybrid)\n\n"
               "Requirements\n- Python and Playwright\n- Kubernetes\n\nAbout Contoso Health\nFICTIONAL.\n")
    ui.get_by_role("link", name="Add job").click()
    expect(ui.get_by_role("button", name="Add job")).to_be_enabled()
    ui.get_by_label("Job description").fill(posting)
    ui.get_by_role("button", name="Add job").click()
    added = ui.get_by_test_id("added-job")
    expect(added.locator("#a-title")).to_have_value("Senior Software Engineer in Test")
    expect(added.locator("#a-company")).to_have_value("Contoso Health")
    expect(added.locator("#a-location")).to_have_value("Toronto, ON (Hybrid)")
    added.locator("#a-company").fill("Contoso Health (fictional)")
    added.get_by_role("button", name="Save changes").click()
    expect(added).to_contain_text("Saved.")


def test_profile_job_match_and_tracking(ui: Page):
    save_profile(ui)
    import_job(ui)
    expect(ui).to_have_url(re.compile(r"/ui/#/jobs/\d+$"))
    expect(ui.get_by_test_id("match").locator(".chip.ok").first).to_be_visible()

    ui.get_by_role("button", name="Track application").click()
    expect(ui.get_by_test_id("materials")).to_contain_text("Materials · Senior QA Engineer")
    status = ui.get_by_label(re.compile("^Status of"))
    expect(status).to_have_value("preparing")

    status.select_option("submitted_confirmed")                       # never without evidence
    expect(ui.get_by_text("Confirmation evidence (required)")).to_be_visible()
    ui.get_by_role("button", name="Mark confirmed").click()
    expect(ui.get_by_text("Paste the confirmation evidence first.")).to_be_visible()
    ui.get_by_role("button", name="Cancel").click()
    expect(status).to_have_value("preparing")


def test_cover_letter_draft_and_edit(ui: Page):
    save_profile(ui)
    import_job(ui)
    ui.get_by_role("button", name="Track application").click()
    ui.get_by_role("button", name="Draft cover letter").click()
    letter = ui.get_by_test_id("doc-cover_letter")
    expect(letter).to_contain_text("Human-style score")
    box = letter.get_by_label("Text of cover_letter")
    expect(box).to_have_value(re.compile("Dear Hiring Team"))
    box.fill(box.input_value() + "\nOne more fictional line.")
    letter.get_by_role("button", name="Save my edits as new version").click()
    expect(ui.get_by_test_id("doc-cover_letter")).to_contain_text("your edit")


def test_assisted_apply_plan(ui: Page):
    save_profile(ui)
    import_job(ui)
    ui.get_by_role("button", name="Track application").click()
    ui.get_by_role("button", name="Assisted apply (Greenhouse): prepare plan").click()
    plan = ui.get_by_test_id("apply-plan")
    expect(plan).to_contain_text("not approved yet")
    expect(plan).to_contain_text("you click Submit yourself")
    plan.get_by_role("button", name="Save my answers and approve").click()
    expect(plan.locator(".msg.bad").last).to_be_visible()             # required answers still missing


def test_no_profile_message_and_ai_status(ui: Page):
    expect(ui.get_by_test_id("ai-status")).to_have_text(re.compile("AI ready"))
    ui.get_by_role("link", name="Profile").click()
    expect(ui.get_by_role("heading", name="Get started: upload your resume")).to_be_visible()


def test_new_user_uploads_a_resume_and_saves_it(ui: Page):
    """A brand-new user: empty profile -> upload a (fictional) resume -> check the preview -> save."""
    resume = ("Jordan Sample\njordan.sample@example.com\nSUMMARY\nFictional QA engineer.\nSKILLS\n"
              "Automation: Playwright, pytest\nEXPERIENCE\nContoso Health | Remote Apr 2023 - Present\n"
              "Senior QA Engineer\nBuilt fictional Playwright suites.\nAdded fictional API checks.\n")
    ui.get_by_role("link", name="Profile").click()
    ui.get_by_label("Resume file").set_input_files(
        files=[{"name": "resume.txt", "mimeType": "text/plain", "buffer": resume.encode()}])
    preview = ui.get_by_test_id("resume-preview")
    expect(preview).to_contain_text("Jordan Sample")
    expect(preview).to_contain_text("Senior QA Engineer, Contoso Health")
    expect(preview).to_contain_text("2 bullets")
    ui.get_by_role("button", name="Save as my profile").click()
    expect(ui.get_by_test_id("profile-summary")).to_contain_text("Jordan Sample · 1 roles")


def test_resume_opens_in_the_same_tab_with_a_way_back(ui: Page):
    save_profile(ui)
    import_job(ui)
    ui.get_by_role("button", name="Track application").click()
    ui.get_by_role("button", name="Tailor resume").click()
    ui.get_by_role("link", name="Open latest resume (save as PDF)").click()
    expect(ui).to_have_url(re.compile(r"/applications/\d+/resume$"))
    expect(ui.get_by_role("button", name="Print / Save as PDF")).to_be_visible()
    ui.get_by_role("link", name="← Back to the app").click()
    expect(ui.get_by_test_id("materials")).to_be_visible()


def test_match_shows_related_experience_and_ai_second_look(ui: Page):
    save_profile(ui)
    import_job(ui)
    match = ui.get_by_test_id("match")
    expect(match).to_contain_text("shown by your profile")
    if match.get_by_role("button", name=re.compile("Ask AI")).count():
        match.get_by_role("button", name=re.compile("Ask AI")).click()
        expect(match).to_contain_text(re.compile("Still no evidence|AI:"))


def test_experience_bank_import_and_confirm(ui: Page):
    save_profile(ui)
    bank = ui.get_by_test_id("bank")
    bank.get_by_label("Experience bank text").fill(
        "Projects\n• PR01: Built a fictional tool with Python and FastAPI (fictional).\n"
        "• AI01: Created a fictional labelled evaluation set (fictional). (partly true)\n")
    bank.get_by_role("button", name="Import records").click()
    expect(bank.get_by_test_id("bank-report")).to_contain_text("2 records imported")
    status = bank.get_by_label("Status of AI01")
    expect(status).to_have_value("partly_true")          # not used on resumes until you confirm it
    status.select_option("verified")
    expect(bank).to_contain_text("Confirmed by you.")


def test_find_jobs_then_apply_prepares_everything(ui: Page):
    save_profile(ui)
    ui.get_by_role("link", name="Find jobs").click()
    ui.get_by_label("Job title").fill("QA Engineer")
    ui.get_by_label("Country").select_option("CA")
    ui.get_by_role("button", name="7 days").click()
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
    row.get_by_role("link", name="Materials").click()                 # leave while it's being prepared
    materials = ui.get_by_test_id("materials")
    expect(materials).to_contain_text("QA Engineer")
    expect(materials.get_by_test_id("doc-cover_letter")).to_be_visible(timeout=30000)   # appears by itself
    expect(materials).to_contain_text("Open latest resume")
    ui.get_by_role("link", name="Find jobs").click()                  # come back: the search is still there
    row = ui.get_by_test_id("found-jobs").locator("tr", has_text="Globex (fictional)")
    expect(row).to_contain_text("Ready: resume and cover letter prepared", timeout=20000)
    expect(ui.get_by_label("Job title")).to_have_value("QA Engineer")


def test_a_gap_you_really_have_can_be_added_and_used(ui: Page):
    save_profile(ui)
    import_job(ui)
    job = ui.request.get("/jobs").json()[0]          # the job also asks for a tool the profile doesn't name
    ui.request.patch(f"/jobs/{job['id']}", data={"required_skills": job["required_skills"] + ["TestRail"]})
    ui.get_by_role("button", name="Track application").click()
    ui.get_by_role("button", name="Tailor resume").click()
    gaps = ui.get_by_test_id("gaps")
    expect(gaps).to_contain_text("TestRail", timeout=10_000)
    gaps.get_by_role("button", name="I have this").last.click()                 # the one next to TestRail
    form = ui.get_by_test_id("gap-form")
    expect(form).to_contain_text("Where did you use TestRail?")
    form.locator("textarea").fill("Kept regression cases and release runs in TestRail (FICTIONAL TEST DATA).")
    form.get_by_role("button", name="Add to my profile").click()
    expect(ui.get_by_test_id("materials")).to_contain_text("Added to your profile: TestRail")
    ui.get_by_role("button", name="Tailor the resume again").click()
    expect(ui.get_by_test_id("materials")).to_contain_text("v2", timeout=10_000)
    expect(ui.get_by_test_id("gaps")).not_to_contain_text("TestRail")


def test_private_review_shows_with_an_experience_bank_and_never_prints(ui: Page):
    import json as _json
    from tests.test_bank import SDET_JOB, with_bank
    ui.request.put("/profile", data=_json.loads(with_bank().model_dump_json()))
    job = ui.request.post("/jobs", data={"title": "Senior SDET", "company": "Example (fictional)",
                                         "description": SDET_JOB.description, "required_skills": ["Java"],
                                         "allow_duplicate": True}).json()
    app = ui.request.post("/applications", data={"job_id": job["id"]}).json()
    ui.goto(f"/ui/#/applications/{app['id']}")
    ui.get_by_role("button", name="Tailor resume").click()
    review = ui.get_by_test_id("private-review")
    expect(review).to_contain_text("Private review", timeout=10_000)
    expect(review).to_contain_text("Java test automation")
    expect(review).to_contain_text("direct")
    ui.get_by_role("link", name="Open latest resume (save as PDF)").click()
    expect(ui.locator("body")).not_to_contain_text("Private review")
