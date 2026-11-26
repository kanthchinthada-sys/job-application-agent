"""Job-specific technical evidence suggestions. Prompts enter the bank's review queue, never its evidence pool."""

import re
import sqlite3

from app import core_skills, evidence, selection
from app.vocabulary import find_terms, is_people_skill

_EXAMPLE = {
    "Test automation": "automate a regression scenario and verify the expected behavior",
    "API testing": "check an endpoint's response and error behavior",
    "Data validation": "check a transformation or reconcile source and target data",
    "Kubernetes and deployment": "verify workload readiness or diagnose a deployment issue",
    "CI/CD": "run a build or regression stage and investigate failures",
    "Languages": "implement a specific test, utility, or service change",
}


def _key(skill: str) -> str:
    return evidence.normalize(skill.casefold())


def _is_technical(skill: str) -> bool:
    return bool(skill and not is_people_skill(skill) and core_skills.category_of(skill) != "Test leadership"
                and (core_skills.is_listable(skill) or any(_key(term) == _key(skill) for _, _, term in find_terms(skill))))


def sync(conn: sqlite3.Connection, job) -> None:
    from app import repository
    from app.errors import NotFoundError
    try:
        profile = repository.get_profile(conn)
    except NotFoundError:
        return
    if not profile.experience and not profile.projects:
        return
    plan = selection.plan(profile, job)
    records = {row.id: row for row in profile.experience_bank if row.status == "verified"}
    by_category = {}
    owners_with_text = [(r.owner, r.text) for r in records.values()]
    owners_with_text += [(owner.company, text) for owner in profile.experience for text in owner.highlights]
    owners_with_text += [(owner.name, text) for owner in profile.projects for text in owner.highlights]
    for owner, text in owners_with_text:
        for _, _, term in find_terms(text):
            if category := core_skills.category_of(term):
                by_category.setdefault(category, owner)
    pending = {}
    alternatives = set()
    for row in plan.rows:
        if row.category != "technical":
            continue
        # A required alternative is already satisfied. Its unused tools are not gaps.
        if row.strength in ("direct", "skills only") and selection._ANY_OF.search(row.requirement) and (
            row.found_parts or row.found_elsewhere
        ):
            alternatives.update(_key(skill) for skill in row.not_found_parts)
            continue
        suggested_owner = next((records[rid].owner for rid in row.selected_ids if rid in records), None)
        for name in row.not_found_parts:
            if _is_technical(name):
                pending.setdefault(_key(name), (row.requirement, suggested_owner))
    for name in [*job.required_skills, *job.preferred_skills]:
        skill = _key(name)
        if skill not in alternatives and _is_technical(name) and not selection._ANY_OF.search(name):
            pending.setdefault(skill, (name, None))
    stated = [(str(i), s) for i, s in enumerate(
        profile.skills + [x for group in profile.skill_groups for x in group.skills]
        + [x for owner in [*profile.experience, *profile.projects] for x in [*owner.highlights, *owner.skills]]
        + [r.text for r in records.values()]
    )]
    for skill in list(pending):
        if (hit := evidence.find_part(skill, stated)) and not hit.related:
            pending.pop(skill)
    for skill, (requirement, owner) in pending.items():
        label = core_skills.display(skill)
        category = core_skills.category_of(skill)
        owner = owner or by_category.get(category)
        action = _EXAMPLE.get(category, "complete a specific technical task")
        example = (f"Draft only if true: Used {label} to {action} for [system or project], "
                   "checked [specific behavior or data], and observed [actual result].")
        conn.execute(
            """INSERT INTO gap_suggestions (job_id, skill, requirement, owner_hint, example)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(job_id, skill) DO UPDATE SET
                 requirement=excluded.requirement, owner_hint=excluded.owner_hint, example=excluded.example""",
            (job.id, label, requirement, owner, example),
        )
    for row in conn.execute("SELECT id, skill FROM gap_suggestions WHERE job_id=? AND state='pending'", (job.id,)):
        if _key(row["skill"]) not in pending:
            conn.execute("DELETE FROM gap_suggestions WHERE id=?", (row["id"],))


def list_suggestions(conn, job_id: int | None = None) -> list[dict]:
    sql = """SELECT s.*, j.title AS job_title, j.company AS job_company FROM gap_suggestions s
             JOIN jobs j ON j.id=s.job_id WHERE s.state='pending'"""
    where = " AND s.job_id=?" if job_id is not None else ""
    return [dict(row) for row in conn.execute(sql + where + " ORDER BY s.id DESC", () if job_id is None else (job_id,))]


def confirm(conn, suggestion_id: int, owner_name: str, text: str) -> tuple[str, int]:
    from app import repository
    from app.errors import ConflictError, NotFoundError
    from app.schemas import ApplicationCreate, BankRecord, Profile

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM gap_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not row:
            raise NotFoundError("That technical gap is no longer on your review list.")
        app_row = conn.execute("SELECT id FROM applications WHERE job_id=?", (row["job_id"],)).fetchone()
        if row["state"] == "approved" and app_row:
            conn.commit()
            return row["record_id"], app_row["id"]
        if row["state"] != "pending":
            raise ConflictError("That technical gap has already been dismissed.")
        skill = row["skill"]
        match = evidence.find_part(_key(skill), [("approved", text)])
        if (not _is_technical(skill) or text == row["example"] or "[" in text or "]" in text
                or re.search(r"\b(if you used|describe how|example if true|draft only if true)\b", text, re.IGNORECASE)
                or match is None or match.related):
            raise ConflictError(f"Describe your real work with {skill} before confirming this record.")
        profile = Profile.model_validate(repository.get_profile(conn).model_dump(exclude={"updated_at"}))
        owners = {x.company.casefold(): (x.company, "experience") for x in profile.experience}
        owners.update({x.name.casefold(): (x.name, "project") for x in profile.projects})
        if owner_name.casefold() not in owners:
            raise ConflictError("Choose an employer or project from your saved profile.")
        owner, kind = owners[owner_name.casefold()]
        records = profile.experience_bank
        existing = next((r for r in records if r.owner == owner and r.text.casefold() == text.casefold()), None)
        if existing and existing.status == "not_true":
            raise ConflictError("That statement was previously marked not true. Enter the corrected experience.")
        if existing:
            rid = existing.id
            records = [r.model_copy(update={"status": "verified", "status_note": "Confirmed from technical gap review"})
                       if r.id == rid else r for r in records]
        else:
            used = {r.id for r in records}
            rid = next((f"GAP{n:02d}" for n in range(1, 1000) if f"GAP{n:02d}" not in used), None)
            if rid is None:
                raise ConflictError("The experience bank has no free IDs for additional technical gap records.")
            records = [*records, BankRecord(id=rid, text=text, owner=owner, owner_kind=kind, group=skill,
                                           status="verified", status_note="Confirmed from technical gap review")]
        repository.save_profile(conn, profile.model_copy(update={"experience_bank": records}), commit=False)
        aid = app_row["id"] if app_row else repository.create_application(
            conn, ApplicationCreate(job_id=row["job_id"]), commit=False).id
        conn.execute("UPDATE gap_suggestions SET state='approved', record_id=? WHERE id=?", (rid, suggestion_id))
        conn.execute("DELETE FROM gap_suggestions WHERE skill=? COLLATE NOCASE AND state='pending'", (skill,))
        conn.execute("INSERT OR IGNORE INTO resume_refresh_requests (application_id) VALUES (?)", (aid,))
        conn.commit()
        return rid, aid
    except Exception:
        conn.rollback()
        raise
