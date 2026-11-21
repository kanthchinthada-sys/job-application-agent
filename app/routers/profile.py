import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints

import base64
import binascii

from app import bank, repository, resume_import
from app.dependencies import get_connection
from app.errors import NotFoundError
from app.schemas import BankRecord, Profile, ProfileOut, RecordStatus, SkillGroup

router = APIRouter(prefix="/profile", tags=["profile"])
Conn = Annotated[sqlite3.Connection, Depends(get_connection)]


@router.get("", response_model=ProfileOut, responses={404: {"description": "No profile saved yet"}})
def read_profile(conn: Conn):
    """Return your saved professional profile."""
    return repository.get_profile(conn)


@router.put("", response_model=ProfileOut)
def replace_profile(profile: Profile, conn: Conn):
    """Create or fully replace your profile.

    PUT (not PATCH) because the profile is one document: send the whole thing.
    Only include experience you actually have; the app never fills gaps for you.
    """
    return repository.save_profile(conn, profile)


class ResumeUpload(BaseModel):
    filename: Annotated[str, StringConstraints(max_length=300)] = "resume.txt"
    content_base64: Annotated[str, StringConstraints(max_length=14_000_000)] | None = None   # ~10 MB file
    text: Annotated[str, StringConstraints(max_length=300_000)] | None = None


@router.post("/resume", response_model=resume_import.ResumeImport,
             responses={409: {"description": "The file could not be read"}, 422: {"description": "Nothing sent"}})
def read_resume(body: ResumeUpload):
    """Read your resume (PDF, Word .docx or text) into a profile PREVIEW. Nothing is saved: review it, then
    save it with PUT /profile. Everything in the preview comes from your resume's own text."""
    from app.errors import ConflictError
    if body.text and body.text.strip():
        text = body.text
    elif body.content_base64:
        try:
            data = base64.b64decode(body.content_base64.split(",")[-1], validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ConflictError("The file could not be decoded.") from exc
        text = resume_import.extract_text(body.filename, data)
    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Send a file or paste your resume text.")
    if not text.strip():
        raise ConflictError("No text found in this file. If it is a scanned PDF, paste the text instead.")
    return resume_import.parse_resume(text)


# ---------------------------------------------------------------------------
# Experience bank: numbered records ("AB01: ...") that resumes are built from
# ---------------------------------------------------------------------------

class BankImport(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=300_000)]


class BankImportOut(BaseModel):
    report: bank.ImportReport
    records: list[BankRecord]


class StatusUpdate(BaseModel):
    status: RecordStatus
    status_note: str | None = None


def _profile_only(p: ProfileOut) -> Profile:
    return Profile(**p.model_dump(exclude={"updated_at"}))


@router.post("/bank", response_model=BankImportOut, responses={404: {"description": "No profile saved yet"}})
def import_bank(body: BankImport, conn: Conn):
    """Import your numbered experience records. Each is routed to its real employer or project, duplicates are
    removed, notes are kept as caveats, and only records marked 'verified' are used on resumes.
    Statuses you changed yourself are kept when you import again."""
    profile = _profile_only(repository.get_profile(conn))
    records, report = bank.parse_bank(body.text, profile)
    merged = bank.merge(profile.experience_bank, records)
    update: dict = {"experience_bank": merged}
    if report.skill_groups:
        # your resume's own Core Skills section replaces the skill groups; every skill joins your skills list
        update["skill_groups"] = [SkillGroup(**g) for g in report.skill_groups]
        names = [x for g in report.skill_groups for x in g["skills"]]
        # older entries stay only if they are named tools; concepts are now covered by your own headings
        from app.selection import KNOWN_TECH
        tools = {t.lower() for t in KNOWN_TECH}
        update["skills"] = list(dict.fromkeys(names + [x for x in profile.skills if x.lower() in tools]))[:200]
    saved = repository.save_profile(conn, Profile(**profile.model_copy(update=update).model_dump()))
    return BankImportOut(report=report, records=saved.experience_bank)


@router.patch("/bank/{record_id}", response_model=BankRecord, responses={404: {"description": "No such record"}})
def set_status(record_id: str, body: StatusUpdate, conn: Conn):
    """Confirm (verified) or exclude (unverified / not_true) one record."""
    profile = _profile_only(repository.get_profile(conn))
    bank_records = list(profile.experience_bank)
    for i, r in enumerate(bank_records):
        if r.id == record_id:
            bank_records[i] = r.model_copy(update={"status": body.status, "status_note": body.status_note})
            repository.save_profile(conn, profile.model_copy(update={"experience_bank": bank_records}))
            return bank_records[i]
    raise NotFoundError(f"No experience record {record_id}.")


class ExperienceLine(BaseModel):
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=600)]
    skill: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None


class ExperienceLineOut(BaseModel):
    id: str | None              # experience-bank record id, when you have a bank
    owner: str
    skill_added: bool


@router.post("/experience-line", response_model=ExperienceLineOut, status_code=201,
             responses={404: {"description": "No profile, or no such employer/project"}})
def add_experience_line(body: ExperienceLine, conn: Conn):
    """Add one line of real experience in your own words (e.g. for a skill a job asks for that your profile
    doesn't mention yet). It is marked verified because you wrote it; the next resume can use it."""
    profile = _profile_only(repository.get_profile(conn))
    exp = next((e for e in profile.experience if e.company.lower() == body.owner.lower()), None)
    proj = next((p for p in profile.projects if p.name.lower() == body.owner.lower()), None)
    if not exp and not proj:
        raise NotFoundError(f"No employer or project called {body.owner!r} in your profile.")
    owner = exp.company if exp else proj.name
    update: dict = {}
    rid = None
    if profile.experience_bank:
        used = {r.id for r in profile.experience_bank}
        rid = next(f"ME{n:02d}" for n in range(1, 1000) if f"ME{n:02d}" not in used)
        update["experience_bank"] = list(profile.experience_bank) + [BankRecord(
            id=rid, text=body.text, owner=owner, owner_kind="experience" if exp else "project",
            group=body.skill or None, status="verified", status_note="Added by you")]
    elif exp:
        update["experience"] = [e.model_copy(update={"highlights": e.highlights + [body.text]}) if e is exp else e
                                for e in profile.experience]
    else:
        update["projects"] = [p.model_copy(update={"highlights": p.highlights + [body.text]}) if p is proj else p
                              for p in profile.projects]
    skill_added = bool(body.skill) and body.skill.lower() not in {s.lower() for s in profile.skills}
    if skill_added:
        update["skills"] = list(profile.skills) + [body.skill]
    repository.save_profile(conn, Profile(**profile.model_copy(update=update).model_dump()))
    return ExperienceLineOut(id=rid, owner=owner, skill_added=skill_added)
