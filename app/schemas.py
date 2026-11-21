"""Pydantic models: the API's contract and its first line of input validation.

FastAPI uses these models to
  1. parse and validate incoming JSON (bad input -> HTTP 422 with details),
  2. shape outgoing JSON, and
  3. generate the interactive documentation at /docs.

Naming convention: ``XCreate`` / ``XUpdate`` are request bodies, ``XOut`` are
responses (they add server-generated fields such as ``id`` and timestamps).
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BeforeValidator,
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

# ---------------------------------------------------------------------------
# Reusable field types
# ---------------------------------------------------------------------------

# A string that is trimmed and must not be blank after trimming.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
YearMonth = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]  # e.g. "2021-07"


def _dedupe_skills(skills: list[str]) -> list[str]:
    """Remove case-insensitive duplicates while keeping the user's spelling and order."""
    seen: set[str] = set()
    result = []
    for skill in skills:
        key = " ".join(skill.lower().split())
        if key not in seen:
            seen.add(key)
            result.append(skill)
    return result


SkillName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
SkillList = Annotated[list[SkillName], Field(max_length=200), AfterValidator(_dedupe_skills)]


class StrictModel(BaseModel):
    # Reject unknown fields so typos like "skils" produce an error instead of
    # being silently dropped. A QA instinct worth keeping.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

class Contact(StrictModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")] | None = None
    phone: NonEmptyStr | None = None
    address: NonEmptyStr | None = Field(default=None, description="Full mailing address, for application forms.")
    location: NonEmptyStr | None = Field(default=None, description="City / region shown on applications.")
    links: list[HttpUrl] = Field(default_factory=list, max_length=20)


class Experience(StrictModel):
    company: NonEmptyStr
    title: NonEmptyStr
    location: NonEmptyStr | None = None
    start_date: YearMonth
    end_date: YearMonth | None = Field(default=None, description="Leave empty for a current role.")
    highlights: list[LongText] = Field(default_factory=list, max_length=50)
    skills: SkillList = Field(default_factory=list)
    recognition: LongText | None = Field(default=None, description="Awards for this role, shown under it.")

    @model_validator(mode="after")
    def end_not_before_start(self):
        # "YYYY-MM" strings sort chronologically, so plain comparison works.
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")
        return self


class SkillGroup(StrictModel):
    name: NonEmptyStr                    # e.g. "QA leadership"
    skills: Annotated[SkillList, Field(min_length=1)]


class Project(StrictModel):
    name: NonEmptyStr
    description: LongText
    skills: SkillList = Field(default_factory=list)
    link: HttpUrl | None = None
    highlights: list[LongText] = Field(default_factory=list, max_length=60,
                                       description="Bullets for the resume (filled per job from the experience bank).")


RecordStatus = Literal["verified", "partly_true", "unverified", "not_true"]


class BankRecord(StrictModel):
    """One numbered line of your experience bank (e.g. AG24). Only 'verified' records ever reach a resume."""

    id: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}\d{2,3}$")]
    text: Annotated[LongText, BeforeValidator(lambda v: " ".join(v.split()) if isinstance(v, str) else v)]
    owner: NonEmptyStr                      # the employer (as in your experience) or project name it belongs to
    owner_kind: Literal["experience", "project"]
    group: NonEmptyStr | None = None        # category label from the bank, e.g. "Performance engineering"
    status: RecordStatus = "unverified"
    status_note: LongText | None = None     # why it has this status
    caveat: LongText | None = None          # measurement context or scope that must be kept (e.g. "design only")


class Education(StrictModel):
    institution: NonEmptyStr
    degree: NonEmptyStr
    location: NonEmptyStr | None = None
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None


class RemotePreference(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    flexible = "flexible"


class JobPreferences(StrictModel):
    target_roles: list[NonEmptyStr] = Field(default_factory=list, max_length=30)
    locations: list[NonEmptyStr] = Field(default_factory=list, max_length=30)
    remote_preference: RemotePreference | None = None
    employment_types: list[NonEmptyStr] = Field(default_factory=list, max_length=10)
    work_authorization: NonEmptyStr | None = Field(default=None, description="As you want it stated on applications.")
    company_boards: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=300)]] = Field(
        default_factory=list, max_length=30,
        description="Greenhouse / Lever / Ashby career-page links that Find jobs also searches.")
    notes: Annotated[str, StringConstraints(max_length=5_000)] = ""


FocusTheme = Literal["automation", "cloud", "ai", "monitoring", "data", "leadership", "performance", "security"]


class ResumeSettings(StrictModel):
    """How resumes are put together for YOU. Every field has a sensible default; change them to taste."""

    min_bullets: list[Annotated[int, Field(ge=0, le=20)]] = Field(
        default_factory=lambda: [6, 4, 3, 3, 2], max_length=10,
        description="Fewest bullets per role, most recent role first (the last number applies to older roles).")
    max_bullets: list[Annotated[int, Field(ge=1, le=25)]] = Field(
        default_factory=lambda: [10, 7, 6, 5, 4], max_length=10,
        description="Most bullets per role, most recent role first.")
    current_role_focus: list[FocusTheme] = Field(
        default_factory=list, max_length=8,
        description="Themes your most recent role should lean towards, weighted by how much the job stresses them.")
    role_priority: dict[NonEmptyStr, list[NonEmptyStr]] = Field(
        default_factory=dict,
        description='Experience-bank records to favour per role type, e.g. {"QA Lead": ["AB01-AB08"]}.')


class Profile(StrictModel):
    """Everything here must come from the user. The app never invents profile data."""

    full_name: NonEmptyStr
    headline: NonEmptyStr | None = None
    summary: LongText | None = None
    contact: Contact = Field(default_factory=Contact)
    skills: SkillList = Field(default_factory=list)
    skill_groups: list[SkillGroup] = Field(
        default_factory=list, max_length=20,
        description="Optional headed skill groups for the resume. Their skills also count as profile skills.",
    )
    experience: list[Experience] = Field(default_factory=list, max_length=50)
    projects: list[Project] = Field(default_factory=list, max_length=50)
    achievements: list[LongText] = Field(default_factory=list, max_length=100)
    education: list[Education] = Field(default_factory=list, max_length=20)
    certifications: list[NonEmptyStr] = Field(default_factory=list, max_length=50)
    preferences: JobPreferences = Field(default_factory=JobPreferences)
    resume_settings: ResumeSettings = Field(default_factory=ResumeSettings)
    experience_bank: list[BankRecord] = Field(
        default_factory=list, max_length=500,
        description="Optional numbered experience records. When present, resumes are built from the verified ones.",
    )


class ProfileOut(Profile):
    updated_at: datetime


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobCreate(StrictModel):
    title: NonEmptyStr
    company: NonEmptyStr
    location: NonEmptyStr | None = None
    source_url: HttpUrl | None = Field(
        default=None, description="Link to the posting. Used to detect the same job being added twice."
    )
    description: LongText
    required_skills: Annotated[SkillList, Field(min_length=1)] = Field(
        description="Entered manually in Milestone 1. Automatic extraction arrives in Milestone 2."
    )
    preferred_skills: SkillList = Field(default_factory=list)
    allow_duplicate: bool = Field(
        default=False, description="Set true to add a job even if one with the same title and company exists."
    )


class JobQuickAdd(StrictModel):
    """Just the link and/or the description: everything else is read from the posting."""

    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20_000)] | None = None
    use_ai: bool = True
    allow_duplicate: bool = False


class JobUpdate(StrictModel):
    description: LongText | None = None
    title: NonEmptyStr | None = None
    company: NonEmptyStr | None = None
    location: NonEmptyStr | None = None
    required_skills: Annotated[SkillList, Field(min_length=1)] | None = None
    preferred_skills: SkillList | None = None


class JobOut(JobCreate):
    id: int
    created_at: datetime
    skills_source: Literal["manual"] = "manual"


# ---------------------------------------------------------------------------
# Skill comparison
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    kind: Literal["declared", "mentioned", "related", "ai"]
    source: str     # e.g. "profile skills", "QA Engineer at Northwind Health (fictional)"
    text: str       # the exact profile text that matched
    via: str = ""   # the word in your profile that showed it (e.g. "etl" for "data pipelines")
    part: str = ""  # which part of the job's requirement it covers (e.g. "python" in "Python/Java/Scala/go")


class SkillResult(BaseModel):
    skill: str              # as written in the job
    normalized: str         # what was actually compared
    evidence: list[Evidence]
    found_parts: list[str] = []     # parts of the requirement with evidence
    missing_parts: list[str] = []   # named parts with no evidence (e.g. "scala")


class SkillSection(BaseModel):
    total: int
    matched: list[SkillResult]         # the same skill appears in your profile
    related: list[SkillResult] = []    # shown by related experience (e.g. ETL work for "data pipelines"): check it
    mentioned_only: list[SkillResult] = []  # kept for compatibility; no longer used
    missing: list[str]                 # no evidence in the profile
    coverage_percent: float | None     # (matched + related) / total; None when total == 0


class MatchReport(BaseModel):
    job_id: int
    method: str
    method_label: str
    required: SkillSection
    preferred: SkillSection
    limitations: list[str]
    profile_updated_at: datetime


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

class ApplicationStatus(str, Enum):
    """Deliberately separates *trying* to submit from *confirmed* submission."""

    preparing = "preparing"                      # materials being prepared
    ready_for_review = "ready_for_review"        # waiting for the user's approval
    submission_attempted = "submission_attempted"  # a submit was tried; outcome unknown yet
    submission_uncertain = "submission_uncertain"  # tried, but we could not confirm the result
    submitted_confirmed = "submitted_confirmed"  # confirmation evidence recorded
    interviewing = "interviewing"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


_RANK = {
    "preparing": 0, "ready_for_review": 1, "submission_attempted": 2, "submission_uncertain": 2,
    "submitted_confirmed": 3, "interviewing": 4, "offer": 5,
}
# Backward moves that make sense (e.g. a failed submission goes back to review).
_ALLOWED_BACK = {
    ("ready_for_review", "preparing"),
    ("submission_attempted", "ready_for_review"), ("submission_uncertain", "ready_for_review"),
    ("submission_uncertain", "submission_attempted"), ("submission_attempted", "submission_uncertain"),
}


def allowed_next(current: "ApplicationStatus") -> list["ApplicationStatus"]:
    """Statuses an application may move to from ``current`` (excluding staying the same)."""
    cur = current.value
    if cur == "rejected":
        return []
    if cur == "withdrawn":
        return [ApplicationStatus.preparing]   # re-open
    out = []
    for status in ApplicationStatus:
        s = status.value
        if s == cur:
            continue
        if s == "withdrawn":
            out.append(status)
        elif s == "rejected":
            if _RANK[cur] >= 2:                # you can only be rejected after applying
                out.append(status)
        elif _RANK[s] > _RANK[cur] or (cur, s) in _ALLOWED_BACK:
            out.append(status)
    return out


class ApplicationCreate(StrictModel):
    job_id: int = Field(gt=0)
    status: ApplicationStatus = ApplicationStatus.preparing
    notes: Annotated[str, StringConstraints(max_length=10_000)] = ""
    submission_confirmation: NonEmptyStr | None = None

    @model_validator(mode="after")
    def confirmed_needs_evidence(self):
        if self.status is ApplicationStatus.submitted_confirmed and not self.submission_confirmation:
            raise ValueError("status 'submitted_confirmed' requires submission_confirmation evidence")
        return self


class ApplicationUpdate(StrictModel):
    """PATCH body: only the fields you send are changed."""

    status: ApplicationStatus | None = None
    notes: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    submission_confirmation: NonEmptyStr | None = Field(
        default=None,
        description="Evidence of submission, e.g. a confirmation number or email subject. "
        "Required to set status 'submitted_confirmed'.",
    )
    event_note: Annotated[str, StringConstraints(max_length=1_000)] | None = Field(
        default=None, description="Optional note stored with this status change in the history."
    )


class ApplicationEvent(BaseModel):
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    note: str | None
    occurred_at: datetime


class ApplicationOut(BaseModel):
    id: int
    job_id: int
    job_title: str
    company: str
    source_url: str | None
    status: ApplicationStatus
    notes: str
    submission_confirmation: str | None
    created_at: datetime
    updated_at: datetime
    history: list[ApplicationEvent]
    allowed_next: list[ApplicationStatus] = []


# ---------------------------------------------------------------------------
# Application materials (versioned documents)
# ---------------------------------------------------------------------------

DocumentKind = Literal["cover_letter", "screening_answer", "resume", "application_plan"]
DraftKind = Literal["cover_letter", "screening_answer", "resume"]


class DraftRequest(StrictModel):
    kind: DraftKind
    question: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1_000)] = ""

    @model_validator(mode="after")
    def question_rules(self):
        if self.kind == "screening_answer" and not self.question:
            raise ValueError("question is required for a screening_answer")
        if self.kind in ("cover_letter", "resume") and self.question:
            raise ValueError(f"question must be empty for a {self.kind}")
        return self


class DocumentEdit(StrictModel):
    """Save your own edited text as a new version."""

    content: LongText


class DocumentOut(BaseModel):
    id: int
    application_id: int
    kind: DocumentKind
    question: str
    version: int
    content: str
    source: Literal["ai", "user_edit", "agent"]
    model: str | None
    review: dict
    created_at: datetime
