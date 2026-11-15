"""Milestone 4: assisted application on Greenhouse.

Flow
----
1. ``build_plan`` reads the job's real application questions from Greenhouse's public API and proposes an
   answer for each one from your profile and your drafted materials. Anything the agent can't know or must not
   decide for you (how you heard about the job, salary, consent checkboxes, demographic questions, sponsorship
   outside Canada) is marked ``needs_you``.
2. You review/edit the plan in the app and approve it (``validate_approval``).
3. ``scripts/apply_greenhouse.py`` (Playwright, on your Mac) fills the real form in a visible browser and
   STOPS. You click Submit yourself.
4. The script reports what happened (``record_outcome``): confirmed (confirmation page seen), uncertain
   (submitted but no confirmation seen), failed (e.g. upload or validation error, nothing submitted) or
   not_submitted. A filled form is never recorded as submitted.
"""

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.errors import ConflictError
from app.importers import Fetcher, parse_link
from app.schemas import ApplicationOut, ApplicationStatus, JobOut, Profile

METHOD = "greenhouse_assisted_v1"

FieldType = Literal["text", "textarea", "file", "single_select", "multi_select", "hidden"]
Source = Literal["profile", "document", "ai", "you", "file"]

_TYPE = {
    "input_text": "text", "textarea": "textarea", "input_file": "file",
    "multi_value_single_select": "single_select", "multi_value_multi_select": "multi_select",
    "input_hidden": "hidden",
}

ALREADY_SUBMITTED = {"submitted_confirmed", "interviewing", "offer", "rejected"}

# Questions the agent must never answer for you.
_CONSENT = re.compile(r"acknowledg|privacy|consent|agree|terms|certif|attest", re.I)
_DEMOGRAPHIC = re.compile(r"gender|race|ethnic|hispanic|veteran|disabilit|sexual|pronoun|demographic", re.I)
_YOU_ONLY = re.compile(r"salary|compensation|pay expectation|notice period|start date|when can you start|"
                       r"hear about|referr|how did you find|relocat|current employer|reference", re.I)
_SPONSOR = re.compile(r"sponsor|visa", re.I)
_AUTHORIZED = re.compile(r"(legally|eligible|authori[sz]ed).{0,40}work|work.{0,20}(permit|authori[sz]ation)", re.I)
_LINKS = re.compile(r"linkedin|website|portfolio|github|blog", re.I)


class PlanField(BaseModel):
    name: str                      # Greenhouse field name, e.g. "first_name" or "question_64278783"
    label: str
    type: FieldType
    required: bool
    options: list[str] = []        # for selects / checkboxes
    answer: str = ""               # what will be filled ("" = leave blank)
    source: Source = "you"
    needs_you: bool = False        # you must answer or confirm this
    sensitive: bool = False        # consent / demographic / legal: never filled by the agent unless you answer
    note: str = ""


class SubmissionPlan(BaseModel):
    method: str = METHOD
    provider: Literal["greenhouse"] = "greenhouse"
    board: str
    job_id: str
    apply_url: str
    fields: list[PlanField]
    approved: bool = False
    warnings: list[str] = []


class PlanEdit(BaseModel):
    """Your answers when approving: field name -> answer."""

    answers: dict[str, str] = Field(default_factory=dict)


class OutcomeReport(BaseModel):
    outcome: Literal["filled", "confirmed", "uncertain", "failed", "not_submitted"]
    evidence: str = ""             # e.g. confirmation page URL + time
    details: str = ""


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------

def greenhouse_ids(job: JobOut) -> tuple[str, str]:
    link = parse_link(str(job.source_url) if job.source_url else "")
    if link.provider != "greenhouse" or not link.job_id:
        raise ConflictError("Assisted apply supports Greenhouse job links only. Use 'Apply by hand' for this job.")
    return link.board, link.job_id


def fetch_questions(board: str, job_id: str, fetch: Fetcher) -> dict:
    return fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true")


def _in_canada(job: JobOut) -> bool:
    from app.preferences import check_location
    return check_location(job.location, ["Canada"])[0] == "yes"


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (full_name, "")


def _propose(name: str, label: str, ftype: str, options: list[str], profile: Profile, job: JobOut,
             screening: dict[str, str]) -> PlanField:
    f = PlanField(name=name, label=label, type=ftype, required=False, options=options)
    c, prefs = profile.contact, profile.preferences
    first, last = _split_name(profile.full_name)
    simple = {"first_name": first, "last_name": last, "email": c.email or "", "phone": c.phone or ""}
    if name in simple:
        f.answer, f.source = simple[name], "profile"
    elif name in ("resume", "resume_text"):
        f.source, f.note = "file", "Your latest tailored resume, uploaded as a PDF."
        f.answer = "tailored_resume.pdf" if name == "resume" else ""
    elif name in ("cover_letter", "cover_letter_text"):
        f.source, f.note = "file", "Your latest cover letter, uploaded as a PDF (if you drafted one)."
        f.answer = "cover_letter.pdf" if name == "cover_letter" else ""
    elif name == "location" or re.search(r"\blocation\b|city", label, re.I) and ftype == "text":
        f.answer, f.source = c.location or "", "profile"
    elif _DEMOGRAPHIC.search(label):
        f.sensitive, f.note = True, "Voluntary demographic question. Left blank unless you choose an answer."
    elif _CONSENT.search(label):
        f.sensitive, f.needs_you = True, True
        f.note = "Consent / acknowledgment: read it on the company's page and answer yourself."
    elif _SPONSOR.search(label):
        f.sensitive = True
        if _in_canada(job) and prefs.work_authorization and re.search(r"permanent resident|citizen", prefs.work_authorization, re.I):
            no = next((o for o in options if o.lower() == "no"), "No")
            f.answer, f.source = no, "profile"
            f.note = f"Based on your work authorization ({prefs.work_authorization}) and a job in Canada. Check it."
        else:
            f.needs_you = True
            f.note = "Depends on the job's country and your status there. Answer yourself."
    elif _AUTHORIZED.search(label):
        f.sensitive = True
        if _in_canada(job) and prefs.work_authorization:
            yes = next((o for o in options if o.lower() == "yes"), "Yes")
            f.answer, f.source = (yes if ftype.endswith("select") else prefs.work_authorization), "profile"
            f.note = f"Based on your work authorization ({prefs.work_authorization}). Check it."
        else:
            f.needs_you = True
    elif _YOU_ONLY.search(label):
        f.needs_you, f.note = True, "Only you know this. The agent won't guess."
    elif _LINKS.search(label):
        f.answer = ", ".join(str(link) for link in c.links)
        f.source = "profile"
    elif ftype in ("text", "textarea"):
        answer = screening.get(label.strip().lower())
        if answer:
            f.answer, f.source, f.note = answer, "document", "From your drafted screening answer. Review it."
        else:
            f.needs_you = True
            f.note = "Custom question. Draft it with AI (Screening question) or type your own answer."
    else:   # unrecognised select / checkbox
        f.needs_you = True
        f.note = "Pick the right option yourself."
    return f


def build_plan(profile: Profile, job: JobOut, gh: dict, screening_answers: dict[str, str]) -> SubmissionPlan:
    board, job_id = greenhouse_ids(job)
    fields: list[PlanField] = []
    groups = [("questions", gh.get("questions") or []), ("location", gh.get("location_questions") or [])]
    for comp in gh.get("compliance") or []:
        groups.append(("compliance", comp.get("questions") or []))
    for group, questions in groups:
        for q in questions:
            for fd in q.get("fields") or []:
                ftype = _TYPE.get(fd.get("type"), "text")
                if ftype == "hidden":
                    continue
                if fd.get("name") in ("resume_text", "cover_letter_text"):
                    continue   # we upload files instead of pasting text
                options = [str(v.get("label")) for v in fd.get("values") or []]
                pf = _propose(fd["name"], q.get("label", fd["name"]), ftype, options, profile, job,
                              {k.lower(): v for k, v in screening_answers.items()})
                pf.required = bool(q.get("required"))
                if group == "compliance":
                    pf.sensitive, pf.needs_you, pf.answer = True, False, ""
                    pf.note = "Voluntary demographic question. Left blank unless you choose an answer."
                if pf.required and not pf.answer and pf.source != "file":
                    pf.needs_you = True
                fields.append(pf)
    if fields and not any(f.name == "country" for f in fields):
        # The real form also asks for Country (not listed by the API).
        country = (profile.contact.location or "").split(",")[-1].strip()
        fields.insert(4, PlanField(name="country", label="Country", type="single_select", required=True,
                                   answer=country, source="profile" if country else "you",
                                   needs_you=not country, note="Only filled if the form asks for it."))
    warnings = []
    if not _in_canada(job):
        warnings.append(f"This job's location ({job.location or 'not stated'}) may not be in Canada. "
                        "Check work-authorization questions carefully.")
    return SubmissionPlan(board=board, job_id=job_id, fields=fields, warnings=warnings,
                          apply_url=f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}")


# ---------------------------------------------------------------------------
# Approval, duplicates, outcomes
# ---------------------------------------------------------------------------

def guard_not_submitted(app: ApplicationOut) -> None:
    if app.status.value in ALREADY_SUBMITTED:
        raise ConflictError(f"This application is already '{app.status.value}'. The agent won't apply twice.")
    if app.status is ApplicationStatus.submission_uncertain:
        raise ConflictError("The last submission attempt is 'uncertain'. Check your email or the company's "
                            "portal first. If it didn't go through, set the status back to 'ready for review'.")


def apply_edits(plan: SubmissionPlan, edit: PlanEdit) -> SubmissionPlan:
    by_name = {f.name: f for f in plan.fields}
    unknown = [n for n in edit.answers if n not in by_name]
    if unknown:
        raise ConflictError(f"Unknown fields: {', '.join(unknown)}")
    for name, answer in edit.answers.items():
        f = by_name[name]
        answer = answer.strip()
        if f.options and answer and f.type in ("single_select", "multi_select"):
            chosen = [a.strip() for a in answer.split(";")] if f.type == "multi_select" else [answer]
            bad = [a for a in chosen if a not in f.options]
            if bad:
                raise ConflictError(f"'{f.label}': '{bad[0]}' is not one of the options: {', '.join(f.options)}")
        f.answer = answer
        f.source = "you" if answer else f.source
        f.needs_you = False if answer else f.needs_you
    return plan


def validate_approval(plan: SubmissionPlan) -> None:
    missing = [f.label for f in plan.fields if f.required and f.type != "file" and not f.answer]
    if missing:
        raise ConflictError("Answer these required questions before approving: " + "; ".join(missing))
    unconfirmed = [f.label for f in plan.fields if f.needs_you and f.required and not f.answer]
    if unconfirmed:
        raise ConflictError("These still need you: " + "; ".join(unconfirmed))


def outcome_to_status(outcome: str) -> ApplicationStatus | None:
    return {
        "confirmed": ApplicationStatus.submitted_confirmed,
        "uncertain": ApplicationStatus.submission_uncertain,
        "filled": None, "failed": None, "not_submitted": None,
    }[outcome]


def apply_domain(url: str) -> str:
    return urlparse(url).netloc
