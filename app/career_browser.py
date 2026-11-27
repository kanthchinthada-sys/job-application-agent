"""Drive supported employer forms in a visible, workspace-specific browser."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from app.errors import ConflictError


class Target(BaseModel):
    provider: str
    key: str
    url: str
    path: str

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    def same_portal(self, url: str) -> bool:
        parsed = urlsplit(url)
        expected = urlsplit(self.url)
        return parsed.scheme == expected.scheme and parsed.netloc.lower() == expected.netloc.lower()

    def on_posting(self, url: str) -> bool:
        path = urlsplit(url).path.rstrip("/")
        intended = self.path
        if self.provider == "workday":
            path, intended = (_without_locale(p) for p in (path, intended))
        return self.same_portal(url) and (path == intended or path.startswith(intended + "/"))


def _without_locale(path: str) -> str:
    return re.sub(r"^/[a-z]{2}-[a-z]{2}(?=/)", "", path, count=1, flags=re.I)


def target_for(url: str) -> Target:
    try:
        u = urlsplit(url)
    except ValueError as exc:
        raise ConflictError("Use the direct HTTPS employer posting URL for auto-apply.") from exc
    host = u.hostname or ""
    if u.scheme != "https" or u.netloc.lower() != host:
        raise ConflictError("Use the direct HTTPS employer posting URL for auto-apply.")
    parts = u.path.strip("/").split("/")
    token = r"[A-Za-z0-9][A-Za-z0-9_\-.]*"
    workday_site = re.fullmatch(r"(?:[a-z0-9][a-z0-9-]*\.wd[0-9]+\.myworkdayjobs|wd[0-9]+\.myworkdaysite)\.com", host)
    if workday_site:
        # Workday localizes and URL-escapes location and title, including non-ASCII characters.
        token = r"(?:[A-Za-z0-9_.~\-]|%(?!2[fF]|5[cC]|2[eE]|25)[0-9A-Fa-f]{2})+"
    if not all(part not in {".", ".."} and re.fullmatch(token, part) for part in parts):
        raise ConflictError("Use the direct job posting URL for auto-apply.")
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}:
        if len(parts) < 3 or parts[1] != "jobs" or not parts[2].isdigit():
            raise ConflictError("Use a Greenhouse posting URL that includes the job number.")
        region = "eu" if ".eu." in host else "global"
        host = "job-boards.eu.greenhouse.io" if region == "eu" else "job-boards.greenhouse.io"
        path = "/" + "/".join(parts[:3])
        return Target(provider="greenhouse", key=f"greenhouse:{region}:{parts[0].lower()}:{parts[2]}",
                      url=f"https://{host}{path}", path=path)
    if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"} and len(parts) >= 2:
        portal = "ashby" if host == "jobs.ashbyhq.com" else "lever"
        path = "/" + "/".join(parts[:2])
        suffix = "/application" if portal == "ashby" else "/apply"
        return Target(provider=portal, key=f"{host}:{parts[0].lower()}:{parts[1].lower()}",
                      url=f"https://{host}{path}{suffix}", path=path)
    if workday_site and "job" in parts:
        job = parts.index("job")
        if 1 <= job and job + 1 < len(parts):
            end = next((i for i in range(job + 1, len(parts)) if parts[i] == "apply"), len(parts))
            path = "/" + "/".join(parts[:end])
            requisition = parts[end - 1].rsplit("_", 1)[-1].lower()
            organization = parts[parts.index("recruiting") + 1] if "recruiting" in parts[:job - 1] else ""
            return Target(provider="workday", key=f"{host}:{organization.lower()}:{requisition}", url=f"https://{host}{path}/apply", path=path)
    raise ConflictError("Auto-apply supports direct Greenhouse, Lever, Ashby, and Workday postings.")


def manual_portal_for(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.netloc.lower() != host:
        return None
    path = parsed.path.rstrip("/")
    if host in {"www.linkedin.com", "ca.linkedin.com", "linkedin.com"} and re.fullmatch(
            r"/jobs/view/(?:[a-zA-Z0-9-]+-)?\d+", path):
        return f"https://{host}{path}"
    if re.fullmatch(r"[a-z0-9-]+\.pinpointhq\.com", host) and re.fullmatch(
            r"/[a-z]{2}/postings/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:/applications/new)?", path):
        return f"https://{host}{path.removesuffix('/applications/new')}/applications/new"
    return None


class Question(BaseModel):
    key: str
    label: str
    options: list[str] = []
    manual: bool = False
    draftable: bool = False
    required: bool = False
    multiple: bool = False
    suggested: str = ""
    source: str = ""


@dataclass
class BrowserResult:
    state: str
    message: str
    questions: list[Question] = field(default_factory=list)
    evidence: str | None = None
    uploaded: set[str] = field(default_factory=set)


_LABELS = {
    "first name": "first_name", "given name": "first_name", "legal first name": "first_name",
    "last name": "last_name", "surname": "last_name", "family name": "last_name", "legal last name": "last_name",
    "name": "full_name", "full name": "full_name", "email": "email", "email address": "email",
    "legal name": "full_name", "legal full name": "full_name", "what is your legal name": "full_name",
    "phone": "phone", "phone number": "phone", "mobile phone": "phone", "mobile phone number": "phone",
    "location": "location", "current location": "location", "location city": "location", "city": "location", "country": "country",
    "current company": "current_company", "current employer": "current_company",
    "linkedin": "linkedin", "linkedin url": "linkedin", "linkedin profile": "linkedin",
    "linked in": "linkedin", "linked in url": "linkedin", "linked in profile": "linkedin",
    "linked in profile url": "linkedin", "please share your linked in profile url": "linkedin",
    "github": "github", "github url": "github", "github profile": "github",
    "git hub": "github", "git hub url": "github", "git hub profile": "github",
    "website": "website", "personal website": "website", "portfolio": "website",
}
_MANUAL = re.compile(r"privacy|consent|agree|acknowledg|term[s ]|attest|certif|signature|gender|ethnic|race\b|"
                     r"pronoun|disabilit|veteran|birth|social security|passport|government id|verification code|"
                     r"personal relationship|family members?|related to.{0,90}(?:anyone|employees?)|"
                     r"describe relationship|conflicts? of interest|potential conflicts?|outside business activit|"
                     r"convicted|criminal record|background check|\bpardon\b", re.I)
_USER_ANSWER = re.compile(r"sponsor|visa|work authorization|eligible|legally|salary|compensation|notice period|"
                          r"when can you start|start date|referr|relocat|how did you hear|years of experience|"
                          r"clearance|on a scale|rate your|citizen|residen[ct]|authorized to work", re.I)
_WRITTEN = re.compile(r"\b(why\b|describe\b|tell us about|share an example|give an example|"
                      r"what (?:interests|draws|motivates)|experience (?:with|in))", re.I)
_CONDITIONAL_FOLLOWUP = re.compile(r"^\s*if (?:yes|no|so|applicable)\b", re.I)


def contact_key(label: str) -> str | None:
    return _LABELS.get(label_key(label))


def requires_browser_review(label: str) -> bool:
    return bool(_MANUAL.search(label))


def can_draft_screening(label: str, kind: str) -> bool:
    return kind in {"textarea", "text", "input"} and bool(_WRITTEN.search(label)) and not (
        requires_browser_review(label) or _USER_ANSWER.search(label)
        or _CONDITIONAL_FOLLOWUP.search(label) or contact_key(label))
_LOGIN = re.compile(r"^(sign in|log in|create (?:(?:an|your) )?account|verify (?:your )?(?:identity|email)|"
                    r"enter (?:the|your) (?:verification|security) code)[.!]?$", re.I)
_CAPTCHA = '[title*="recaptcha challenge" i], [title*="hCaptcha challenge" i], [title*="security challenge" i]'
_CONFIRMED = re.compile(r"thank(?:s| you) for (applying|submitting your application|your application)|"
                        r"your application (?:was |has been )?(?:successfully )?(?:submitted|received)|"
                        r"we(?:'ve| have) received your application|application submitted successfully|"
                        r"successfully submitted your application", re.I)
_CONFIRM_HEADING = re.compile(r"^application (?:submitted|received)[!.]?$", re.I)
_DUPLICATE = re.compile(r"(?:you (?:have |already )?|you.ve )(?:already )?applied for this (?:job|position)|"
                        r"application already submitted", re.I)
_SUBMIT = re.compile(r"^(submit(?: (?:my )?application)?|send application)$", re.I)
_SUBMIT_TERMS = re.compile(r"\bby (?:clicking|selecting|pressing)\s+.{0,15}?submit\b.{0,110}"
                           r"\b(?:agree to|accept)\s+(?:the |our )?(?:terms(?: and conditions)?|legal agreement)\b", re.I)
_CONTINUE = re.compile(r"^(save (?:and|&) continue|continue|next|review application)$", re.I)
_START_ACTIONS = (re.compile(r"^(?:autofill|apply) with (?:a )?r[eé]sum[eé]$", re.I),
                  re.compile(r"^apply manually$", re.I), re.compile(r"^apply(?: now)?$", re.I))

_SCAN = r"""() => {
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const controls = [...document.querySelectorAll('input, textarea, select, [role=combobox], button[aria-haspopup=listbox], [contenteditable=true]')];
  const text = el => el?.innerText?.trim() || '';
  return controls.flatMap((el, index) => {
    let type = el.tagName === 'SELECT' ? 'select' :
      (el.getAttribute('aria-haspopup') === 'listbox' ? 'combobox' : null) ||
      el.getAttribute('role') || el.getAttribute('type') || el.tagName.toLowerCase();
    if (/^(hidden|submit|reset|button|image|password|search)$/.test(type) || el.disabled || el.readOnly) return [];
    const parent = el.closest('[data-automation-id^=formField], .application-question, fieldset');
    const approvedUploadControl = type === 'file' && /^(resume|cover_letter)$/.test(el.id) && !!el.closest('form');
    if (!approvedUploadControl && !visible(el) && (type !== 'file' || !visible(el.parentElement))) return [];
    if (!approvedUploadControl && el.closest('[aria-hidden=true]')) return [];
    const option = [...(el.labels || [])].map(text).filter(Boolean).join(' ');
    const legend = text(el.closest('fieldset')?.querySelector('legend'));
    const group = el.closest('[role=radiogroup], [role=group]');
    const groupLabel = group?.getAttribute('aria-label') || text(document.getElementById(group?.getAttribute('aria-labelledby')));
    const aria = text(document.getElementById(el.getAttribute('aria-labelledby')));
    const cardLabel = el.name?.startsWith('cards[') ? text(el.closest('.custom-question')?.querySelector('.application-label')) : '';
    const label = cardLabel || ((type === 'radio' || type === 'checkbox') ? (legend || groupLabel) : '') ||
      el.getAttribute('aria-label') || aria || option || text(parent?.querySelector('label')) ||
      el.getAttribute('placeholder') || el.name || el.id || 'Unlabelled field';
    const required = !!(el.required || el.getAttribute('aria-required') === 'true' ||
      /[*✱]$/.test(label.trim()) || group?.getAttribute('aria-required') === 'true');
    const marker = String(index);
    el.setAttribute('data-job-agent-control', marker);
    const shown = el.tagName === 'BUTTON' ? text(el) : '';
    const value = shown && !/^(select( one| an option)?|choose( one| an option)?|please select)$/i.test(shown) ? shown : el.value;
    const fileScope = el.closest('[data-automation-id*=resume i], [data-automation-id*=coverLetter i]');
    const selectScope = el.closest('.select__control');
    const selected = [...(selectScope?.querySelectorAll('.select__single-value, .select__multi-value__label') || [])].map(text).filter(Boolean);
    return [{marker, name: (type === 'combobox' ? el.id : '') || el.name || el.id || el.getAttribute('data-automation-id') || '',
      fileHint: fileScope?.getAttribute('data-automation-id') || '',
      label: label.slice(0, 500), required, type, option, selected,
      value: selected.join('; ') || String(value ?? (el.isContentEditable ? text(el) : '')),
      options: type === 'select' ? [...el.options].filter(o => o.value && !o.disabled).slice(0, 100).map(o => o.label) : [],
      checked: !!el.checked, files: el.files ? [...el.files].map(f => f.name) : [],
      valid: !el.validity || el.validity.valid}];
  });
}"""


def label_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", text)).casefold().split())


def saved_answer_key(name: str, label: str) -> str:
    return f"approved:{name}:{label_key(label)}"


def _field_key(controls) -> str:
    first = controls[0]
    return hashlib.sha256(json.dumps([first["name"], first["label"], first["type"]]).encode()).hexdigest()[:20]


def _document_kind(control) -> str | None:
    field_name = control["name"].casefold()
    if field_name in {"resume", "cover_letter"}:
        return field_name
    name = label_key(f'{control["label"]} {field_name.replace("_", " ")} {control["fileHint"]}')
    if "cover letter" in name:
        return "cover_letter"
    if re.search(r"\b(?:resum[eé]|cv)\b", name):
        return "resume"
    return None


def _button(page, pattern):
    choices = [button for button in page.get_by_role("button", name=pattern).all() if button.is_visible()]
    if len(choices) == 1:
        return choices[0]
    return None


def _signature(page) -> tuple[str, str, tuple]:
    headings = page.get_by_role("heading").all_text_contents()
    fields = page.evaluate(_SCAN)
    return page.url, "|".join(headings), tuple((f["name"], f["label"], f["type"]) for f in fields)


_REGIONS = {"on": "ontario", "bc": "british columbia", "ab": "alberta", "qc": "quebec", "pq": "quebec",
            "mb": "manitoba", "ns": "nova scotia", "nb": "new brunswick", "sk": "saskatchewan",
            "pe": "prince edward island", "nl": "newfoundland and labrador", "ca": "canada"}


def _option_text(wanted: str, options: list[str], location: bool, country: bool = False) -> str | None:
    if country:
        matches = [o for o in options if label_key(re.sub(r"\s*\+[\d\s()-]+$", "", o)) == label_key(wanted)]
        return matches[0] if len(matches) == 1 else None
    matches = [o for o in options if label_key(o) == label_key(wanted)]
    if not matches and location:
        def parts(text):
            values = [label_key(x) for x in text.split(",") if label_key(x)]
            return values[:1] + [_REGIONS.get(x, x) for x in values[1:]]

        requested = parts(wanted)
        matches = [o for o in options if (candidate := parts(o)) and requested and candidate[0] == requested[0]
                   and set(requested[1:]).issubset(candidate[1:])]
    return matches[0] if len(matches) == 1 else None


def _dropdown_menu(page, field):
    menu_id = field.get_attribute("aria-controls") or field.get_attribute("aria-owns")
    if menu_id:
        return page.locator(f'[id={json.dumps(menu_id)}]')
    return page.get_by_role("listbox").filter(visible=True)


def _dropdown(page, field, first: dict, value: str) -> tuple[bool, list[str]]:
    from playwright.sync_api import Error

    values = [v.strip() for v in value.split(";") if v.strip()] if first["name"].endswith("[]") else [value] if value else []
    contact = contact_key(first["label"])
    location = contact == "location"
    country = contact == "country"
    if values and len(first["selected"]) == len(values) and all(
            _option_text(v, first["selected"], location, country) for v in values):
        return True, first["selected"]
    options: list[str] = []
    try:
        widget = page.locator(".select__control").filter(has=field)
        if values and first["name"].endswith("[]") and first["selected"] and widget.count() == 1:
            remove = widget.locator(".select__multi-value__remove")
            for _ in first["selected"]:
                if not remove.count():
                    break
                remove.first.click(timeout=2000)
        for wanted in values or [""]:
            field.click(timeout=2000)
            if wanted and field.evaluate("el => el instanceof HTMLInputElement"):
                field.fill(wanted.split(",", 1)[0] if location else wanted)
            menu = _dropdown_menu(page, field)
            choices = menu.get_by_role("option").filter(visible=True)
            if not location and wanted:
                name = re.compile(rf"^{re.escape(wanted)}(?:\s*\+[\d\s()-]+)?$", re.I) if country else wanted
                exact = menu.get_by_role("option", name=name, exact=True).filter(visible=True)
                exact.wait_for(state="visible", timeout=2500)
            else:
                choices.first.wait_for(state="visible", timeout=4000)
            labels = [c.inner_text().strip() for c in choices.all()]
            options = list(dict.fromkeys(o for o in labels if o))
            if not wanted:
                break
            match = _option_text(wanted, options, location, country)
            if not match:
                field.press("Escape")
                return False, options
            menu.get_by_role("option", name=match, exact=True).filter(visible=True).click(timeout=2500)
        if values and widget.count() == 1:
            selected = widget.locator(".select__single-value, .select__multi-value__label").all_inner_texts()
            if country and not _option_text(values[0], selected, location, country):
                # Greenhouse displays only the dialing code in the collapsed country control.
                field.click(timeout=2000)
                selected_option = _dropdown_menu(page, field).locator('[role=option][aria-selected=true], .select__option--is-selected')
                selected = selected_option.filter(visible=True).all_inner_texts()
                field.press("Escape", timeout=1000)
            return len(selected) == len(values) and all(_option_text(v, selected, location, country) for v in values), options
        if values:
            return True, options
    except Error:
        pass
    try:
        if field.get_attribute("aria-expanded") != "false":
            field.press("Escape", timeout=1000)
    except Error:
        pass
    return False, options


def _progressed(page, previous, seconds: float = 8) -> bool:
    from playwright.sync_api import Error

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if _signature(page) != previous:
                return True
        except Error:
            pass
        page.wait_for_timeout(250)
    return False


class CareerBrowser:
    """One persistent browser profile per local workspace. Only its worker thread calls Playwright."""

    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir
        self._pw = self._context = None
        self._pages: dict[int, tuple[object, str, set[str]]] = {}

    def close(self):
        if self._context:
            self._context.close()
        if self._pw:
            self._pw.stop()
        self._pw = self._context = None
        self._pages.clear()

    def _closed(self, _context):
        self._context = None
        self._pages.clear()

    def _page(self, application_id, target, session_key):
        if self._context is None:
            from playwright.sync_api import sync_playwright
            self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.profile_dir.chmod(0o700)
            if self._pw is None:
                self._pw = sync_playwright().start()
            options = {"headless": False, "viewport": {"width": 1300, "height": 900}, "accept_downloads": False}
            if exe := os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
                options["executable_path"] = exe
            self._context = self._pw.chromium.launch_persistent_context(str(self.profile_dir), **options)
            self._context.on("close", self._closed)
            self._context.set_default_timeout(6000)
            self._context.set_default_navigation_timeout(15000)
        previous = self._pages.get(application_id)
        if previous and previous[1] == session_key and not previous[0].is_closed():
            return previous[0], previous[2], True
        if previous and not previous[0].is_closed():
            previous[0].close()
        page = self._context.new_page()
        uploaded: set[str] = set()
        self._pages[application_id] = page, session_key, uploaded
        page.goto(target.url, wait_until="domcontentloaded")
        return page, uploaded, False

    def run(self, application_id: int, target: Target, contact: dict[str, str], answers: dict[str, str],
            files: dict[str, dict], session_key: str, browser_reviewed: bool, mark_attempt: Callable[[], None]):
        page, uploaded, same_tab = self._page(application_id, target, session_key)
        page.bring_to_front()
        result = run_form(page, target, contact, answers, files, uploaded, browser_reviewed and same_tab, mark_attempt)
        result.uploaded = uploaded.copy()
        if result.state in {"confirmed", "uncertain", "already_applied"} and not page.is_closed():
            # A retry must never reuse a confirmation as its starting page.
            self._pages.pop(application_id, None)
        return result


def _fill(page, target, fields, contact, answers, files, uploaded, reviewed) -> list[Question]:
    groups: dict[tuple, list[dict]] = {}
    for control in fields:
        group = (control["name"] or control["marker"], control["label"], control["type"])
        groups.setdefault(group, []).append(control)
    questions, optional_drafts = [], []
    for controls in groups.values():
        first = controls[0]
        label, kind, legacy_key = first["label"], first["type"], _field_key(controls)
        reviewed_key = saved_answer_key(first["name"], label)
        greenhouse_question = target.provider == "greenhouse" and first["name"].startswith("question_")
        key = reviewed_key if greenhouse_question else legacy_key
        manual = (requires_browser_review(label) or kind == "file"
                  or target.provider == "lever" and first["name"].startswith(("pronouns", "surveysResponses[")))
        options = first["options"] or [c["option"] for c in controls if c["option"]] if kind in {"select", "checkbox", "radio"} else []
        required = any(c["required"] for c in controls)
        question = Question(key=key, label=label, options=list(dict.fromkeys(options)), manual=manual, required=required,
                            multiple=(kind == "combobox" and first["name"].endswith("[]")),
                            draftable=not manual and can_draft_screening(label, kind))
        field = page.locator(f'[data-job-agent-control="{first["marker"]}"]')
        if kind == "file":
            doc = _document_kind(first)
            if doc in files:
                payload = files[doc]
                if payload["name"] not in first["files"]:
                    field.set_input_files(payload)
                from playwright.sync_api import Error
                try:
                    selected = field.evaluate("el => Array.from(el.files || [], f => f.name)", timeout=1500)
                except Error:
                    selected = []
                receipt = page.get_by_text(payload["name"], exact=True).first
                if payload["name"] in selected:
                    uploaded.add(doc)
                else:
                    try:
                        receipt.wait_for(state="visible", timeout=3000)
                        uploaded.add(doc)
                    except Error:
                        questions.append(question)
            elif (first["required"] and not first["files"]) or (first["files"] and not reviewed):
                questions.append(question)
            continue
        expected = contact_key(label)
        if not manual and reviewed_key in answers and not answers[reviewed_key] and not required \
                and kind in {"input", "text", "textarea", "url", "email", "tel"}:
            if first["value"]:
                field.fill("")
            continue
        primary, fallback = (reviewed_key, legacy_key) if greenhouse_question else (legacy_key, reviewed_key)
        value = "" if manual else answers.get(primary, answers.get(fallback, contact.get(expected or "", "")))
        if value:
            if kind == "select":
                matches = [choice for choice in options if label_key(choice) == label_key(value)]
                if len(matches) == 1:
                    field.select_option(label=matches[0])
                    continue
            elif kind in {"radio", "checkbox"}:
                chosen = {label_key(s) for s in value.split(";") if s.strip()}
                matches = [c for c in controls if label_key(c["option"]) in chosen]
                if len(matches) == len(chosen) and (kind == "checkbox" or len(matches) == 1):
                    for c in controls:
                        control = page.locator(f'[data-job-agent-control="{c["marker"]}"]')
                        if c in matches:
                            control.check()
                        elif kind == "checkbox" and c["checked"]:
                            control.uncheck()
                    continue
            elif kind == "combobox":
                selected, options = _dropdown(page, field, first, value)
                if selected:
                    continue
                question.options = options
            else:
                field.fill(value)
                continue
        present = any(c["checked"] for c in controls) if kind in {"radio", "checkbox"} else bool(first["value"].strip())
        if kind == "combobox" and required and not present and not value and not manual:
            _, question.options = _dropdown(page, field, first, "")
        if value or (required and not present) or (not reviewed and present):
            questions.append(question)
        elif target.provider == "lever" and question.draftable and not present:
            optional_drafts.append(question)
    return questions + optional_drafts if questions else []


def run_form(page, target, contact, answers, files, uploaded, reviewed, mark_attempt, *, confirm_seconds=25):
    """Fill known fields, stop for exceptions, and click an identified final submit at most once."""
    for _ in range(12):
        if not target.same_portal(page.url):
            return BrowserResult("needs_you", "The portal opened a different site. Return to the approved employer form in the browser before resuming.")
        body = page.locator("body").inner_text(timeout=6000)
        if _DUPLICATE.search(body) or _CONFIRMED.search(body) or page.get_by_role("heading", name=_CONFIRM_HEADING).count():
            return BrowserResult("already_applied", "The employer page already shows an application or confirmation. Check the portal before continuing.")
        if any(p.is_visible() for p in page.locator('input[type=password], input[autocomplete=one-time-code], ' + _CAPTCHA).all()) or any(
                heading.is_visible() for heading in page.get_by_role("heading", name=_LOGIN).all()):
            return BrowserResult("needs_you", "Complete sign-in, registration, or verification in the open browser, then resume here.")
        if not target.on_posting(page.url):
            return BrowserResult("needs_you", "Return to the approved job’s application in the open browser, then resume here.")
        before = _signature(page)
        fields = page.evaluate(_SCAN)
        final = _button(page, _SUBMIT)
        start = next((b for pattern in _START_ACTIONS if (b := _button(page, pattern))), None) if not fields else None
        if start:
            start.click()
            if _progressed(page, before):
                reviewed = False
                continue
        questions = _fill(page, target, fields, contact, answers, files, uploaded, reviewed)
        if questions:
            return BrowserResult("needs_you", "Review the questions below or complete them in the open employer form.", questions)
        invalid = [f for f in page.evaluate(_SCAN) if not f["valid"]]
        if invalid:
            return BrowserResult("needs_you", "The employer form rejected some values. Correct them in the open browser.",
                                 [Question(key=_field_key([f]), label=f["label"], manual=True) for f in invalid])
        if final:
            if _SUBMIT_TERMS.search(" ".join(body.split())):
                return BrowserResult("needs_you", "This employer’s Submit button also accepts terms. Read the terms and complete that step in the employer browser.")
            if "resume" not in uploaded or ("cover_letter" in files and "cover_letter" not in uploaded):
                return BrowserResult("needs_you", "The form did not accept all selected PDFs. Check its attachment fields or change the selected documents.")
            if target.provider == "workday" and not reviewed:
                return BrowserResult("needs_you", "Review Workday’s final page in the browser, including any experience it parsed from your résumé. Resume here when it is correct.")
            # Read the action again after form changes, while the page is still bound to the approved job.
            if not target.on_posting(page.url) or not final.is_enabled() or final.evaluate(
                    "b => { const a = b.getAttribute('formaction') || b.form?.getAttribute('action');"
                    " return !!a && new URL(a, location.href).origin !== location.origin; }"):
                return BrowserResult("needs_you", "The portal’s final action could not be verified. Review the form in the browser.")
            mark_attempt()
            final.click(timeout=8000)
            end = time.monotonic() + confirm_seconds
            while not page.is_closed() and time.monotonic() < end:
                if target.same_portal(page.url) and (_CONFIRMED.search(page.locator("body").inner_text(timeout=3000))
                                                     or page.get_by_role("heading", name=_CONFIRM_HEADING).count()):
                    u = urlsplit(page.url)
                    evidence = f"Employer confirmation on {urlunsplit((u.scheme, u.netloc, u.path, '', ''))}"
                    return BrowserResult("confirmed", "The employer confirmed the submission.", evidence=evidence, uploaded=uploaded.copy())
                page.wait_for_timeout(300)
            return BrowserResult("uncertain", "Submit was attempted. Check the employer portal or email before retrying.")
        next_step = _button(page, _CONTINUE) if target.provider == "workday" else None
        if next_step and (fields or reviewed) and next_step.is_enabled():
            next_step.click()
            if _progressed(page, before):
                reviewed = False
                continue
        return BrowserResult("needs_you", "The application form or its next action needs review in the open browser.")
    return BrowserResult("needs_you", "The portal has additional steps. Review the next step in the open browser.")
