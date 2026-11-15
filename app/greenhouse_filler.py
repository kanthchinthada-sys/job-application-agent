"""Fill a Greenhouse application form in a real browser. It NEVER clicks Submit.

Used by scripts/apply_greenhouse.py. Kept separate so the logic can be tested against a fake form
(tests/e2e/test_assisted_apply.py) without ever touching a real employer's site.

Greenhouse form conventions (checked against a live form, Nov 2026):
  * inputs have the API field name as their id: #first_name, #question_64278783, ...
  * single selects are searchable comboboxes (role=combobox): click, type the option, press Enter
  * multi-select checkboxes have ids like "question_123[]_<value id>" and a label with the option text
  * files: <input type=file id="resume"> / id="cover_letter"
  * the submit button is labelled "Submit application"
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

CONFIRMATION_TEXT = re.compile(
    r"thank you for (applying|your application)|application (has been )?(submitted|received)|we('ve| have) received",
    re.I,
)


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)     # left blank on purpose (e.g. demographic questions)
    not_found: list[str] = field(default_factory=list)   # in the plan but not on the page
    problems: list[str] = field(default_factory=list)    # e.g. upload failed

    @property
    def ok(self) -> bool:
        return not self.problems


def _field(page: Page, name: str):
    return page.locator(f'[id="{name}"]')


def _fill_text(page: Page, name: str, value: str) -> bool:
    loc = _field(page, name)
    if loc.count() == 0:
        return False
    loc.first.fill(value)
    return True


def _fill_combobox(page: Page, name: str, value: str) -> bool:
    loc = _field(page, name)
    if loc.count() == 0:
        return False
    box = loc.first
    box.click()
    box.fill(value)
    option = page.get_by_role("option", name=value, exact=True)
    try:
        option.first.wait_for(timeout=3000)
        option.first.click()
    except PlaywrightError:
        box.press("Enter")   # some comboboxes accept typed text
    return True


def _tick_checkboxes(page: Page, name: str, labels: list[str]) -> list[str]:
    """Tick the checkboxes whose label matches. Returns labels that weren't found."""
    missing = []
    for label in labels:
        target = page.get_by_role("checkbox", name=label, exact=True)
        if target.count() == 0:
            missing.append(label)
            continue
        target.first.check()
    return missing


def _upload(page: Page, name: str, path: Path, report: FillReport, retries: int = 1) -> None:
    loc = _field(page, name)
    if loc.count() == 0:
        report.not_found.append(name)
        return
    for attempt in range(retries + 1):
        loc.first.set_input_files(str(path))
        try:   # the form shows the file name once the upload is accepted
            page.get_by_text(path.name).first.wait_for(timeout=8000)
            report.filled.append(name)
            return
        except PlaywrightError:
            if attempt == retries:
                report.problems.append(f"Upload of {path.name} into '{name}' was not accepted by the form.")


_IGNORED_POSTS = re.compile(r"analytics|collect|telemetry|sentry|recaptcha|log|metrics|beacon", re.I)


def _track_posts(page: Page) -> dict:
    """Remember form submissions (POSTs) on this page from the moment we start, so none is missed."""
    state = getattr(page, "_job_agent_posts", None)
    if state is not None:
        return state
    state = {"posted_at": None, "last_status": None}

    def on_response(response):
        req = response.request
        if req.method == "POST" and req.resource_type in ("document", "fetch", "xhr") and not _IGNORED_POSTS.search(req.url):
            state["posted_at"] = state["posted_at"] or time.time()
            state["last_status"] = response.status

    page.on("response", on_response)
    page._job_agent_posts = state
    return state


def fill_form(page: Page, plan: dict, files: dict[str, Path]) -> FillReport:
    """Fill every planned answer. Leaves blanks blank. Does not submit."""
    report = FillReport()
    _track_posts(page)
    page.goto(plan["apply_url"])
    page.locator("form").first.wait_for(timeout=15000)
    for f in plan["fields"]:
        name, ftype, answer = f["name"], f["type"], (f.get("answer") or "").strip()
        if ftype == "file":
            if name in files:
                _upload(page, name, files[name], report)
            else:
                report.skipped.append(name)
            continue
        if not answer:
            report.skipped.append(name)
            continue
        if ftype in ("text", "textarea"):
            found = _fill_text(page, name, answer)
        elif ftype == "single_select":
            found = _fill_combobox(page, name, answer)
        elif ftype == "multi_select":
            missing = _tick_checkboxes(page, name, [a.strip() for a in answer.split(";") if a.strip()])
            found = not missing
            if missing:
                report.problems.append(f"Could not find option(s) {missing} for '{f['label']}'.")
        else:
            found = False
        (report.filled if found else report.not_found).append(name)
    return report


@dataclass
class Outcome:
    outcome: str          # confirmed | uncertain | not_submitted
    evidence: str = ""
    details: str = ""


def watch_for_submission(page: Page, timeout_s: float = 1800, confirm_wait_s: float = 60) -> Outcome:
    """Wait for YOU to click Submit, then decide what happened.

    * confirmation page/text seen       -> confirmed (evidence: URL + time)
    * submit request sent, no confirmation within ``confirm_wait_s`` -> uncertain
    * submit request answered with an error -> keeps waiting (you can fix the form and resubmit)
    * browser/page closed without submitting -> not_submitted
    """
    state = _track_posts(page)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.is_closed():
            if state["posted_at"]:
                return Outcome("uncertain", details="Browser closed after submitting, before a confirmation was seen.")
            return Outcome("not_submitted", details="Browser closed without submitting.")
        try:
            url = page.url
            text = page.locator("body").inner_text(timeout=2000)
        except PlaywrightError:
            time.sleep(0.5)
            continue
        if "confirmation" in url.lower() or CONFIRMATION_TEXT.search(text):
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            return Outcome("confirmed", evidence=f"Confirmation page {url} seen at {stamp}")
        if state["posted_at"] and state["last_status"] and state["last_status"] < 400 \
                and time.time() - state["posted_at"] > confirm_wait_s:
            return Outcome("uncertain", details="Submitted, but no confirmation page was seen. Check your email.")
        time.sleep(0.5)
    return Outcome("not_submitted", details="Timed out waiting for you to submit.")
