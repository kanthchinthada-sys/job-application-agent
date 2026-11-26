"""All SQL lives here.

Routers call these functions; they never write SQL themselves. That separation
means a later move to PostgreSQL (or an ORM such as SQLAlchemy) changes this
file and database.py, not the API.

All queries use ``?`` placeholders, never string formatting, which prevents
SQL injection.
"""

import json
import sqlite3
from datetime import UTC, datetime

from app.errors import ConflictError, NotFoundError
from app.schemas import (
    DocumentOut,
    ApplicationCreate,
    ApplicationDetails,
    ApplicationOut,
    ApplicationStatus,
    ApplicationUpdate,
    allowed_next,
    JobCreate,
    JobOut,
    Profile,
    ProfileOut,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def save_profile(conn: sqlite3.Connection, profile: Profile, *, commit: bool = True) -> ProfileOut:
    now = _now()
    data = profile.model_dump_json(exclude={"updated_at"})
    conn.execute(
        """INSERT INTO profile (id, data, updated_at) VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
        (data, now),
    )
    if commit:
        conn.commit()
    return get_profile(conn)


def get_profile(conn: sqlite3.Connection) -> ProfileOut:
    row = conn.execute("SELECT data, updated_at FROM profile WHERE id = 1").fetchone()
    if row is None:
        raise NotFoundError("No profile saved yet. Create one with PUT /profile.")
    return ProfileOut(**json.loads(row["data"]), updated_at=row["updated_at"])


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _row_to_job(row: sqlite3.Row) -> JobOut:
    return JobOut(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        source_url=row["source_url"],
        description=row["description"],
        required_skills=json.loads(row["required_skills"]),
        preferred_skills=json.loads(row["preferred_skills"]),
        created_at=row["created_at"],
    )


def create_job(conn: sqlite3.Connection, job: JobCreate) -> JobOut:
    source_url = str(job.source_url) if job.source_url else None
    if source_url:
        existing = conn.execute("SELECT id FROM jobs WHERE source_url = ?", (source_url,)).fetchone()
        if existing:
            raise ConflictError(
                f"A job with this source_url already exists (job id {existing['id']}).",
                existing_id=existing["id"],
            )
    if not job.allow_duplicate:
        same = conn.execute(
            "SELECT id FROM jobs WHERE lower(trim(title)) = lower(?) AND lower(trim(company)) = lower(?)",
            (job.title.strip(), job.company.strip()),
        ).fetchone()
        if same:
            raise ConflictError(
                f"You already have '{job.title}' at {job.company} (job id {same['id']}). "
                "If this really is a different posting, add it again with allow_duplicate.",
                existing_id=same["id"],
            )
    cur = conn.execute(
        """INSERT INTO jobs (title, company, location, source_url, description,
                             required_skills, preferred_skills, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job.title,
            job.company,
            job.location,
            source_url,
            job.description,
            json.dumps(job.required_skills),
            json.dumps(job.preferred_skills),
            _now(),
        ),
    )
    result = get_job(conn, cur.lastrowid)
    from app import gap_review
    gap_review.sync(conn, result)
    conn.commit()
    return get_job(conn, result.id)


def get_job(conn: sqlite3.Connection, job_id: int) -> JobOut:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Job {job_id} not found.")
    out = _row_to_job(row)
    out.pending_technical_gaps = conn.execute(
        "SELECT COUNT(*) FROM gap_suggestions WHERE job_id=? AND state='pending'", (job_id,)).fetchone()[0]
    return out


def update_job(conn: sqlite3.Connection, job_id: int, changes: dict) -> JobOut:
    """Change title, company, location or skills of a saved job (fixing what was read from the posting)."""
    get_job(conn, job_id)
    allowed = {"title", "company", "location", "description", "required_skills", "preferred_skills"}
    sets, values = [], []
    for key, value in changes.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        values.append(json.dumps(value) if key.endswith("_skills") else value)
    if sets:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*values, job_id))
        if set(changes) & {"description", "required_skills", "preferred_skills"}:
            from app import gap_review
            gap_review.sync(conn, get_job(conn, job_id))
        conn.commit()
    return get_job(conn, job_id)


def list_jobs(conn: sqlite3.Connection) -> list[JobOut]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    return [_row_to_job(r) for r in rows]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

def _record_event(conn, application_id, from_status, to_status, note, when) -> None:
    conn.execute(
        """INSERT INTO application_events (application_id, from_status, to_status, note, occurred_at)
           VALUES (?, ?, ?, ?, ?)""",
        (application_id, from_status, to_status, note, when),
    )


def create_application(conn: sqlite3.Connection, data: ApplicationCreate, *, commit: bool = True) -> ApplicationOut:
    get_job(conn, data.job_id)  # raises NotFoundError if the job does not exist

    existing = conn.execute("SELECT id FROM applications WHERE job_id = ?", (data.job_id,)).fetchone()
    if existing:
        raise ConflictError(
            f"An application for job {data.job_id} already exists (application id {existing['id']}). "
            "Update it instead of creating a duplicate.",
            existing_id=existing["id"],
        )

    now = _now()
    try:
        cur = conn.execute(
            """INSERT INTO applications (job_id, status, notes, submission_confirmation, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data.job_id, data.status.value, data.notes, data.submission_confirmation, now, now),
        )
    except sqlite3.IntegrityError as exc:
        # Safety net: the UNIQUE(job_id) constraint catches a race between the check above and the insert.
        conn.rollback()
        raise ConflictError(f"An application for job {data.job_id} already exists.") from exc
    _record_event(conn, cur.lastrowid, None, data.status.value, "created", now)
    if commit:
        conn.commit()
    return get_application(conn, cur.lastrowid)


_APP_SELECT = """
    SELECT a.*, j.title AS job_title, j.company AS company, j.source_url AS source_url
       , t.next_action, t.due_date, t.interview_at, t.submitted_resume_id, t.submitted_cover_letter_id
    FROM applications a JOIN jobs j ON j.id = a.job_id
    LEFT JOIN application_details t ON t.application_id = a.id
"""


def _row_to_application(conn, row: sqlite3.Row) -> ApplicationOut:
    events = conn.execute(
        "SELECT from_status, to_status, note, occurred_at FROM application_events "
        "WHERE application_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    return ApplicationOut(
        id=row["id"],
        job_id=row["job_id"],
        job_title=row["job_title"],
        company=row["company"],
        source_url=row["source_url"],
        status=row["status"],
        notes=row["notes"],
        submission_confirmation=row["submission_confirmation"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        history=[dict(e) for e in events],
        **{key: row[key] for key in ApplicationDetails.model_fields},
        allowed_next=allowed_next(ApplicationStatus(row["status"])),
        documents_ready=[r[0] for r in conn.execute(
            "SELECT DISTINCT kind FROM documents WHERE application_id = ? AND kind IN ('resume', 'cover_letter') "
            "ORDER BY kind DESC", (row["id"],))],
        last_activity=max([row["updated_at"]] + [r[0] for r in conn.execute(
            "SELECT MAX(created_at) FROM documents WHERE application_id = ?", (row["id"],)) if r[0]]),
    )


def get_application(conn: sqlite3.Connection, application_id: int) -> ApplicationOut:
    row = conn.execute(_APP_SELECT + " WHERE a.id = ?", (application_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Application {application_id} not found.")
    return _row_to_application(conn, row)


def list_applications(conn: sqlite3.Connection, status: ApplicationStatus | None = None) -> list[ApplicationOut]:
    if status is None:
        rows = conn.execute(_APP_SELECT + " ORDER BY a.id").fetchall()
    else:
        rows = conn.execute(_APP_SELECT + " WHERE a.status = ? ORDER BY a.id", (status.value,)).fetchall()
    return [_row_to_application(conn, r) for r in rows]


def update_application(conn: sqlite3.Connection, application_id: int, patch: ApplicationUpdate) -> ApplicationOut:
    current = get_application(conn, application_id)

    new_status = patch.status or current.status
    new_notes = patch.notes if patch.notes is not None else current.notes
    new_confirmation = patch.submission_confirmation or current.submission_confirmation

    if new_status is not current.status and new_status not in current.allowed_next:
        allowed = ", ".join(s.value for s in current.allowed_next) or "none (final status)"
        raise ConflictError(
            f"Can't move from '{current.status.value}' to '{new_status.value}'. Allowed next: {allowed}."
        )

    # Core business rule: "confirmed" needs evidence, never just "a form was filled".
    if new_status is ApplicationStatus.submitted_confirmed and not new_confirmation:
        raise ConflictError(
            "Cannot mark as 'submitted_confirmed' without submission_confirmation evidence "
            "(e.g. a confirmation number or email). Use 'submission_attempted' or "
            "'submission_uncertain' until you have it."
        )

    details = patch.model_dump(mode="json", include=ApplicationDetails.model_fields.keys(), exclude_unset=True)
    if details:
        for field, kind in (("submitted_resume_id", "resume"), ("submitted_cover_letter_id", "cover_letter")):
            if details.get(field) is not None:
                doc = get_document(conn, details[field])
                if doc.application_id != application_id or doc.kind != kind:
                    raise ConflictError(f"Choose a {kind.replace('_', ' ')} version from this application.")
        values = current.model_dump(mode="json", include=ApplicationDetails.model_fields.keys()) | details
        conn.execute(
            """INSERT INTO application_details (application_id, next_action, due_date, interview_at,
                   submitted_resume_id, submitted_cover_letter_id) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(application_id) DO UPDATE SET next_action=excluded.next_action,
                   due_date=excluded.due_date, interview_at=excluded.interview_at,
                   submitted_resume_id=excluded.submitted_resume_id,
                   submitted_cover_letter_id=excluded.submitted_cover_letter_id""",
            (application_id, *(values[key] for key in ApplicationDetails.model_fields)),
        )
    now = _now()
    conn.execute(
        "UPDATE applications SET status = ?, notes = ?, submission_confirmation = ?, updated_at = ? WHERE id = ?",
        (new_status.value, new_notes, new_confirmation, now, application_id),
    )
    if new_status is not current.status:
        _record_event(conn, application_id, current.status.value, new_status.value, patch.event_note, now)
    conn.commit()
    return get_application(conn, application_id)


# ---------------------------------------------------------------------------
# Documents (application materials). Append-only: each save is a new version.
# ---------------------------------------------------------------------------

def _row_to_document(row: sqlite3.Row) -> DocumentOut:
    return DocumentOut(**{**dict(row), "review": json.loads(row["review"])})


def add_document(conn, application_id: int, kind: str, question: str, content: str,
                 source: str, model: str | None, review: dict) -> DocumentOut:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        get_application(conn, application_id)  # 404 if missing
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM documents WHERE application_id = ? AND kind = ? AND question = ?",
            (application_id, kind, question),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO documents (application_id, kind, question, version, content, source, model, review, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (application_id, kind, question, row["v"] + 1, content, source, model, json.dumps(review), _now()),
        )
    return get_document(conn, cur.lastrowid)


def get_document(conn, document_id: int) -> DocumentOut:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Document {document_id} not found.")
    return _row_to_document(row)


def list_documents(conn, application_id: int) -> list[DocumentOut]:
    get_application(conn, application_id)
    rows = conn.execute(
        "SELECT * FROM documents WHERE application_id = ? ORDER BY kind, question, version DESC", (application_id,)
    ).fetchall()
    return [_row_to_document(r) for r in rows]


def add_note(conn: sqlite3.Connection, application_id: int, note: str) -> None:
    """Record something that happened without changing the status (shown in the history)."""
    current = get_application(conn, application_id)
    _record_event(conn, application_id, current.status.value, current.status.value, note, _now())
    conn.commit()
