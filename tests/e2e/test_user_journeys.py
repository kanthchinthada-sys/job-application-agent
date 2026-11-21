"""User journeys through the browser screen, end to end. FICTIONAL data only."""

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
GH_JOB_URL = "https://boards.greenhouse.io/examplecorp/jobs/111"     # served by the fake fetcher


# ---------- helpers (each one is a small, reusable user action) ----------

def save_fictional_profile(page: Page, target_roles=("QA Engineer", "SDET"), locations=("Canada",)):
    page.goto("/")
    page.get_by_role("button", name="Profile").click()
    page.get_by_role("button", name="Load fictional example").click()
    expect(page.locator("#profile-json")).to_have_value(re.compile(re.escape("FICTIONAL")))       # wait until it has loaded
    data = json.loads(page.locator("#profile-json").input_value())
    data["preferences"] = {"target_roles": list(target_roles), "locations": list(locations)}
    page.locator("#profile-json").fill(json.dumps(data, indent=2))
    page.get_by_role("button", name="Save profile").click()
    expect(page.locator("#profile-msg")).to_have_text("Profile saved.")


def import_and_save_job(page: Page, url=GH_JOB_URL):
    page.get_by_role("button", name="Add job").click()
    page.locator("#imp-url").fill(url)
    page.get_by_role("button", name="Import", exact=True).click()
    expect(page.locator("#extract-msg")).to_contain_text("Found 2 required")      # AI ran automatically
    page.get_by_role("button", name="Save job").click()
    expect(page.locator("#match-card")).to_be_visible()


def track_first_job(page: Page):
    page.get_by_role("button", name="Jobs", exact=True).click()
    page.get_by_role("button", name="Track application").first.click()
    expect(page.locator("#apps-list select")).to_be_visible()


def open_materials(page: Page):
    page.get_by_role("button", name="Materials").first.click()
    expect(page.locator("#materials-card")).to_be_visible()


# ---------- journeys ----------

def test_profile_save_and_invalid_json(page: Page):
    save_fictional_profile(page)
    page.reload()
    page.get_by_role("button", name="Profile").click()
    expect(page.locator("#profile-json")).to_have_value(re.compile(re.escape("Jordan Sample (FICTIONAL TEST DATA)")))

    page.locator("#profile-json").fill("Jordan Sample\nresume text, not JSON")
    page.get_by_role("button", name="Save profile").click()
    expect(page.locator("#profile-msg")).to_contain_text("needs the profile in JSON format")


def test_import_job_from_link_then_skill_match(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    match = page.locator("#match-card")
    expect(match).to_contain_text("Rule-based evidence comparison")
    expect(match.locator(".chip.ok").first).to_be_visible()                     # at least one matched skill
    expect(match).to_contain_text("How this comparison works")


def test_import_shows_preference_fit_and_unsupported_link(page: Page):
    save_fictional_profile(page)
    page.get_by_role("button", name="Add job").click()
    page.locator("#imp-url").fill(GH_JOB_URL)
    page.get_by_role("button", name="Import", exact=True).click()
    expect(page.locator("#imp-msg")).to_contain_text("matches your target role")
    expect(page.locator("#imp-msg")).to_contain_text("in Canada (toronto)")
    expect(page.locator("#j-title")).to_have_value("Senior QA Engineer")

    page.locator("#imp-url").fill("https://www.linkedin.com/jobs/view/123")
    page.get_by_role("button", name="Import", exact=True).click()
    expect(page.locator("#imp-msg")).to_contain_text("isn't supported yet")


def test_board_search_filters_to_suitable_jobs(page: Page):
    save_fictional_profile(page)
    page.get_by_role("button", name="Add job").click()
    page.locator("#board-url").fill("https://boards.greenhouse.io/examplecorp")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#board-msg")).to_have_text("1 of 3 open postings shown.")
    expect(page.locator("#board-results")).to_contain_text("Senior QA Engineer")
    expect(page.locator("#board-results")).not_to_contain_text("Account Executive")


def test_duplicate_job_is_caught(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    # Same title + company again, without a link: the app asks before adding a duplicate.
    page.get_by_role("button", name="Add job").click()
    page.locator("#j-title").fill("Senior QA Engineer")
    page.locator("#j-company").fill("Examplecorp (fictional)")
    page.locator("#j-desc").fill("FICTIONAL. Same posting pasted a second time by mistake.")
    page.locator("#j-required").fill("Python")
    page.once("dialog", lambda d: d.dismiss())                                  # "Add it anyway?" -> No
    page.get_by_role("button", name="Save job").click()
    expect(page.locator("#save-job-msg")).to_have_text("You already have 'Senior QA Engineer' at Examplecorp (fictional) (job id 1).")
    assert len(page.request.get("/jobs").json()) == 1


def test_status_rules_and_confirmed_submission_needs_evidence(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    track_first_job(page)
    select = page.locator("#apps-list select").first
    options = select.locator("option").all_text_contents()
    assert "rejected" not in options                                            # can't be rejected before applying

    page.once("dialog", lambda d: d.dismiss())                                  # no evidence given
    select.select_option("submitted_confirmed")
    expect(page.locator("#apps-list select").first).to_have_value("preparing")

    page.once("dialog", lambda d: d.accept("FAKE-CONFIRMATION-42"))
    page.locator("#apps-list select").first.select_option("submitted_confirmed")
    expect(page.locator("#apps-list")).to_contain_text("Confirmation: FAKE-CONFIRMATION-42")
    options = page.locator("#apps-list select").first.locator("option").all_text_contents()
    assert "preparing" not in options                                           # no going back after submitting


def test_cover_letter_draft_checks_and_versions(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    track_first_job(page)
    open_materials(page)
    page.get_by_role("button", name="Draft cover letter").click()
    docs = page.locator("#docs")
    expect(docs).to_contain_text("Cover letter", timeout=10_000)
    expect(docs).to_contain_text("Human-style score")
    expect(docs).to_contain_text("rewritten by the humanizer")                 # "I am thrilled" got revised
    letter = docs.locator("textarea").first
    expect(letter).to_have_value(re.compile(re.escape("Dear Hiring Team,")))
    expect(letter).to_have_value(re.compile(re.escape("Sincerely,\nJordan Sample (FICTIONAL TEST DATA)")))

    letter.fill("My own edited letter.")
    page.get_by_role("button", name="Save my edits as new version").first.click()
    expect(docs).to_contain_text("v2 · your edit")
    expect(docs).to_contain_text("Older versions (1)")


def test_tailored_resume_page(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    track_first_job(page)
    open_materials(page)
    page.get_by_role("button", name="Tailor resume").click()
    expect(page.locator("#docs")).to_contain_text("ATS keyword match", timeout=10_000)

    # Opens in the same tab (browsers built into editors block new tabs), with a way back.
    page.get_by_role("link", name=re.compile("Open latest resume")).click()
    resume = page
    expect(resume).to_have_url(re.compile(r"/applications/\d+/resume$"))
    expect(resume.locator("h1")).to_have_text("Jordan Sample (FICTIONAL TEST DATA)")
    for heading in ["Professional Summary", "Core Skills", "Professional Experience", "Projects"]:
        expect(resume.get_by_role("heading", name=heading)).to_be_visible()
    expect(resume.locator("body")).to_contain_text("Example Corp (fictional)")
    resume.get_by_role("link", name="← Back to the app").click()
    expect(page.get_by_role("heading", name="Job Application Agent")).to_be_visible()


def test_apply_by_hand_checklist_and_uncertain_outcome(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    track_first_job(page)
    open_materials(page)
    page.get_by_role("button", name="Apply by hand: checklist").click()
    box = page.locator("#handoff")
    expect(box).to_contain_text("Apply by hand at Examplecorp (fictional)")
    expect(box).to_contain_text("No tailored resume yet")
    expect(box.get_by_role("link", name="Open the posting")).to_have_attribute("href", re.compile("greenhouse"))
    box.get_by_role("button", name="Not sure it went through").click()
    expect(page.locator("#apps-list select").first).to_have_value("submission_uncertain")


def test_ai_offline_is_reported_clearly(page: Page, fake_llm_e2e):
    fake_llm_e2e.offline = True
    page.goto("/")
    expect(page.locator("#ai")).to_have_text("AI offline (open the Ollama app)")
    page.get_by_role("button", name="Add job").click()
    page.locator("#j-desc").fill("FICTIONAL posting text that is long enough to send to the AI.")
    page.get_by_role("button", name="Find skills with AI").click()
    expect(page.locator("#extract-msg")).to_contain_text("Is the Ollama app running?")


def test_duplicate_can_be_added_on_purpose(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)
    page.get_by_role("button", name="Add job").click()
    page.locator("#j-title").fill("Senior QA Engineer")
    page.locator("#j-company").fill("Examplecorp (fictional)")
    page.locator("#j-desc").fill("FICTIONAL. A genuinely different posting with the same title.")
    page.locator("#j-required").fill("Python")
    page.once("dialog", lambda d: d.accept())                                   # "Add it anyway?" -> Yes
    page.get_by_role("button", name="Save job").click()
    expect(page.locator("#match-card")).to_be_visible()
    assert len(page.request.get("/jobs").json()) == 2


def test_assisted_apply_plan_review_and_approval(page: Page):
    save_fictional_profile(page)
    import_and_save_job(page)                     # a Greenhouse job
    track_first_job(page)
    open_materials(page)
    page.get_by_role("button", name="Assisted apply (Greenhouse): prepare plan").click()
    plan = page.locator("#plan")
    expect(plan).to_contain_text("Assisted apply plan")
    expect(plan).to_contain_text("not approved yet")
    expect(plan.locator("[data-field=first_name]")).to_have_value("Jordan")
    expect(plan.locator("[data-field=gender]")).to_have_value("")                     # demographic left blank
    expect(plan.get_by_role("checkbox", name="Acknowledge/Confirm")).not_to_be_checked()   # consent never auto

    page.get_by_role("button", name="Save my answers and approve").click()
    expect(page.locator("#plan-msg")).to_contain_text("How did you hear about this job?")  # required, only you know

    plan.locator("[data-field=question_1]").fill("Company website")
    plan.locator("[data-field=phone]").fill("+1 555 0100")                         # the fictional profile has no phone
    plan.locator("[data-field=question_3]").select_option("No")                    # ...and no work authorization
    plan.get_by_role("checkbox", name="Acknowledge/Confirm").check()
    page.get_by_role("button", name="Save my answers and approve").click()
    expect(plan).to_contain_text("v2 · approved")
    expect(plan).to_contain_text("python scripts/apply_greenhouse.py --application 1")
    expect(page.locator("#apps-list select").first).to_have_value("ready_for_review")

