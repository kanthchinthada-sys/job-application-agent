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

# Skills with no category above: a concrete draft to adapt, so the prompt says what kind of work would count.
# {label} is the posting's own spelling; every draft names the skill, so an edited draft still shows it.
_DRAFTS = {
    "incident response": "Handled {label} for [system]: triaged [alert or report], found the root cause in "
                         "[component], and restored service in [time]",
    "compliance": "Produced {label} evidence for [regulation or standard] on [system], such as [audit records, "
                  "access controls, or test results]",
    "devsecops": "Added {label} checks ([dependency, image, or secret] scanning) to the [CI/CD tool] pipeline for "
                 "[system], which caught [issue]",
    "ai agents": "Built or tested {label} in [system or project] that call [tools or APIs] to [task], and checked "
                 "[behavior or result]",
    "distributed systems": "Tested or debugged {label} in [system]: [queues, retries, replication, or failover], "
                           "and found [result]",
    "observability": "Added {label} to [system] with [metrics, logs, or traces in which tool], which found [problem]",
    "microservices": "Built, tested, or debugged {label} in [system] that call each other through [APIs or "
                     "messaging], and [result]",
    "security testing": "Ran {label} on [system] with [tool or method], found [issue], and verified the fix",
    "performance testing": "Ran {label} on [system] with [tool] at [load], found [bottleneck], and [result]",
}


# Which jobs have had their gaps worked out, and by which version of the review. A job saved before gap review
# existed (or reviewed by an older version) is reviewed again the first time it is opened (ensure); it takes under
# a second. Raise REVIEW_VERSION when the rules or the prompts change.
REVIEW_VERSION = 2
_RUNS = """CREATE TABLE IF NOT EXISTS gap_review_runs (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE, synced_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1)"""


def _runs_table(conn: sqlite3.Connection) -> None:
    conn.execute(_RUNS)
    if "version" not in {row[1] for row in conn.execute("PRAGMA table_info(gap_review_runs)")}:
        conn.execute("ALTER TABLE gap_review_runs ADD COLUMN version INTEGER NOT NULL DEFAULT 1")


def ensure(conn: sqlite3.Connection, job) -> bool:
    """Work out this job's technical gaps if that was never done with the current review. True if it ran."""
    _runs_table(conn)
    if conn.execute("SELECT 1 FROM gap_review_runs WHERE job_id=? AND version>=?",
                    (job.id, REVIEW_VERSION)).fetchone():
        return False
    sync(conn, job)
    conn.commit()
    return True


def _key(skill: str) -> str:
    return evidence.normalize(skill.casefold())


def _label(skill: str, job, requirement: str = "", heading: bool = True) -> str:
    """The skill as the posting spells it ("DevSecOps", "AI agents"), not the lowercase matching form. As a heading
    a lowercase term starts with a capital ("Incident response"); inside a sentence it stays as written."""
    shown = core_skills.display(skill)
    if shown != shown.lower():
        return shown
    for name in [*job.required_skills, *job.preferred_skills]:
        if _key(name) == _key(skill) and name != name.lower():
            return name
    m = re.search(rf"\b{re.escape(shown)}\b", f"{requirement}\n{job.description or ''}", re.IGNORECASE)
    if m and m.group(0) != m.group(0).lower():
        return m.group(0)
    return shown[:1].upper() + shown[1:] if heading else shown


def _example(skill: str, label: str) -> str:
    """A draft to adapt, only if it is true: concrete enough to show what kind of work would count."""
    category = core_skills.category_of(skill)
    if category in _EXAMPLE:
        body = (f"Used {label} to {_EXAMPLE[category]} for [system or project], checked [specific behavior or data], "
                "and observed [actual result]")
    elif _key(skill) in _DRAFTS:
        body = _DRAFTS[_key(skill)].format(label=label)
    else:
        body = f"Used {label} to [build, test, or fix what] in [system or project], and [the result you saw]"
    return f"Draft only if true: {body}."


def _parts(name: str) -> list[str]:
    """A combined skill from the posting's list, checked part by part: "Docker/GitHub Actions/Azure" is three
    skills, and "version control (Git)" is Git. A known term with a slash ("CI/CD") stays whole."""
    if any(_key(term) == _key(name) for _, _, term in find_terms(name)):
        return [name]
    if m := re.fullmatch(r"\s*(.+?)\s*\((.+)\)\s*", name):
        return _parts(m.group(2)) if _is_technical(m.group(2)) else _parts(m.group(1))
    parts = [x.strip() for x in name.split("/") if x.strip()]
    if len(parts) > 1 and all(len(x) > 2 for x in parts):
        return [x for part in parts for x in _parts(part)]
    return [name]


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
    stated = [(str(i), s) for i, s in enumerate(
        profile.skills + [x for group in profile.skill_groups for x in group.skills]
        + [x for owner in [*profile.experience, *profile.projects] for x in [*owner.highlights, *owner.skills]]
        + [r.text for r in records.values()]
    )]
    # what the match table already found in your records ("Kubernetes, distributed systems, and APIs": all three)
    covered = {_key(part) for row in plan.rows for part in [*row.found_parts, *row.found_elsewhere]}

    def have(name: str) -> bool:
        return _key(name) in covered or bool((hit := evidence.find_part(_key(name), stated)) and not hit.related)

    pending = {}
    alternatives = set()
    # "TestNG/JUnit", "Power BI/Databricks": either one meets it, so none is a gap when you have one of them
    either = [[_key(x) for x in _parts(listed)] for listed in [*job.required_skills, *job.preferred_skills]]
    for row in plan.rows:
        for group in re.findall(r"[\w.+#-]+(?:\s*/\s*[\w.+#-]+)+", row.requirement):
            either.append([_key(x) for x in group.split("/") if x.strip()])
    either = [group for group in either if len(group) > 1]

    def add(name: str, requirement: str, owner) -> None:
        for part in [x for x in _parts(name) if _is_technical(x)]:
            key = _key(part)
            if any(key in group and any(have(other) for other in group if other != key) for group in either):
                continue
            if key not in alternatives and not selection._ANY_OF.search(part) and not have(part):
                pending.setdefault(key, (requirement, owner))

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
            add(name, row.requirement, suggested_owner)
    for listed in [*job.required_skills, *job.preferred_skills]:
        add(listed, listed, None)
    for skill, (requirement, owner) in pending.items():
        label = _label(skill, job, requirement)
        owner = owner or by_category.get(core_skills.category_of(skill))
        example = _example(skill, _label(skill, job, requirement, heading=False))
        conn.execute(
            """INSERT INTO gap_suggestions (job_id, skill, requirement, owner_hint, example)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(job_id, skill) DO UPDATE SET skill=excluded.skill,
                 requirement=excluded.requirement, owner_hint=excluded.owner_hint, example=excluded.example""",
            (job.id, label, requirement, owner, example),
        )
    for row in conn.execute("SELECT id, skill FROM gap_suggestions WHERE job_id=? AND state='pending'", (job.id,)):
        if _key(row["skill"]) not in pending:
            conn.execute("DELETE FROM gap_suggestions WHERE id=?", (row["id"],))
    from datetime import datetime, timezone
    _runs_table(conn)
    conn.execute("INSERT OR REPLACE INTO gap_review_runs (job_id, synced_at, version) VALUES (?, ?, ?)",
                 (job.id, datetime.now(timezone.utc).isoformat(), REVIEW_VERSION))


def list_suggestions(conn, job_id: int | None = None) -> list[dict]:
    _columns(conn)
    sql = """SELECT s.*, j.title AS job_title, j.company AS job_company FROM gap_suggestions s
             JOIN jobs j ON j.id=s.job_id WHERE s.state='pending'"""
    where = " AND s.job_id=?" if job_id is not None else ""
    return [dict(row) for row in conn.execute(sql + where + " ORDER BY s.id DESC", () if job_id is None else (job_id,))]


def confirm(conn, suggestion_id: int, owner_name: str, text: str, in_transaction: bool = False) -> tuple[str, int]:
    """Add one confirmed line to the bank and ask for a new resume for its job. in_transaction: part of
    confirm_many, which opens and commits one transaction for all of them."""
    from app import repository
    from app.errors import ConflictError, NotFoundError
    from app.schemas import ApplicationCreate, BankRecord, Profile

    if not in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM gap_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not row:
            raise NotFoundError("That technical gap is no longer on your review list.")
        app_row = conn.execute("SELECT id FROM applications WHERE job_id=?", (row["job_id"],)).fetchone()
        if row["state"] == "approved" and app_row:
            in_transaction or conn.commit()
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
        in_transaction or conn.commit()
        return rid, aid
    except Exception:
        if not in_transaction:
            conn.rollback()
        raise


def confirm_many(conn, items: list[tuple[int, str, str]]) -> tuple[list[str], list[int]]:
    """Add several confirmed lines at once (all or none), so each job's resume is written once for all of them."""
    from app.errors import ConflictError
    conn.execute("BEGIN IMMEDIATE")
    try:
        records, apps = [], []
        for suggestion_id, owner_name, text in items:
            skill = (conn.execute("SELECT skill FROM gap_suggestions WHERE id=?", (suggestion_id,)).fetchone()
                     or {"skill": "this skill"})["skill"]
            try:
                rid, aid = confirm(conn, suggestion_id, owner_name, text, in_transaction=True)
            except ConflictError as exc:
                raise ConflictError(f"{skill}: {exc}") from exc
            records.append(rid)
            if aid not in apps:
                apps.append(aid)
        conn.commit()
        return records, apps
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------------------------------------------
# AI drafts: instead of asking you to write the line, the AI writes one, set at the employer or project where the
# skill most plausibly fits, built on what your records there already show. It only joins your bank (and reaches a
# resume) after you confirm it is true.

_DRAFT_SYSTEM = """You help a job seeker add one line to their experience bank for a skill a job posting asks for.
Write ONE resume bullet, set in the candidate's real work at the employer or project where this skill most plausibly
fits, building on the systems, tools and domain that employer's records already show.
Rules:
- Name the skill exactly as given, once.
- One sentence, past tense, starts with a strong action verb, 16-28 words, no first person.
- No numbers, percentages, team sizes, awards, certifications, or leadership claims (led, managed, mentored, owned).
- Stay modest and concrete: the kind of work someone in that role there could really have done. The candidate will
  confirm it is true before it is used.
- owner must be exactly one of the listed names; based_on lists the record IDs the bullet builds on.
- why: one short sentence saying why this employer or project fits."""

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "owner": {"type": "string"},
        "text": {"type": "string"},
        "based_on": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string"},
    },
    "required": ["owner", "text", "based_on", "why"],
}


def _columns(conn: sqlite3.Connection) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(gap_suggestions)")}
    for column in ("draft_owner", "draft_text", "draft_note"):
        if column not in have:
            conn.execute(f"ALTER TABLE gap_suggestions ADD COLUMN {column} TEXT")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#.]+", evidence.normalize(text.casefold())) if len(w) > 2}


def _candidates(profile, job, row) -> list[dict]:
    """Your employers and projects, most relevant to this skill first, each with its closest records."""
    focus = _words(f"{row['skill']} {row['requirement']} {job.title}")
    category = core_skills.category_of(row["skill"])
    owners: dict[str, dict] = {}
    for e in profile.experience:
        owners.setdefault(e.company, {"name": e.company, "kind": "experience", "role": e.title,
                                      "dates": f"{e.start_date} to {e.end_date or 'present'}", "lines": []})
        owners[e.company]["lines"] += [(None, h) for h in e.highlights]
    for p in profile.projects:
        owners.setdefault(p.name, {"name": p.name, "kind": "project", "role": "personal project", "dates": "",
                                   "lines": [(None, p.description)]})
        owners[p.name]["lines"] += [(None, h) for h in p.highlights]
    for r in profile.experience_bank:
        if r.status == "verified" and r.owner in owners and not r.caveat:
            owners[r.owner]["lines"].append((r.id, r.text))
    ranked = []
    for o in owners.values():
        def score(line):
            words = _words(line)
            same_area = category and any(core_skills.category_of(t) == category for _, _, t in find_terms(line))
            return len(words & focus) + (3 if same_area else 0)
        lines = sorted({t: (rid, t) for rid, t in o["lines"]}.values(), key=lambda x: -score(x[1]))
        o["lines"] = lines[:6]
        o["score"] = sum(score(t) for _, t in lines[:3]) + (5 if o["name"] == row["owner_hint"] else 0)
        ranked.append(o)
    ranked.sort(key=lambda o: -o["score"])
    return ranked[:4]


def _draft_problems(text: str, skill: str, label: str) -> list[str]:
    from app import claims
    problems = []
    hit = evidence.find_part(_key(skill), [("draft", text)])
    if hit is None or hit.related:
        problems.append(f"It must name {label}.")
    if claims._NUMBER.search(text) or claims.NUMBER_WORDS.search(text):
        problems.append("It must not contain numbers or amounts.")
    if claims.LEADERSHIP.search(text):
        problems.append("It must not claim leadership (led, managed, mentored).")
    if "[" in text or "]" in text:
        problems.append("It must be a finished sentence with no placeholders.")
    if not 12 <= len(text.split()) <= 30:
        problems.append(f"It must be 16-28 words (it has {len(text.split())}).")
    return problems


def draft(conn: sqlite3.Connection, llm, suggestion_id: int, again: bool = False) -> dict:
    """The AI's line for this gap (written once and kept; again=True writes a new one)."""
    from app import metrics, repository
    from app.errors import NotFoundError
    from app.llm import LLMOutputError
    _columns(conn)
    row = conn.execute("SELECT * FROM gap_suggestions WHERE id=? AND state='pending'", (suggestion_id,)).fetchone()
    if not row:
        raise NotFoundError("That technical gap is no longer on your review list.")
    if row["draft_text"] and not again:
        return dict(row)
    job = repository.get_job(conn, row["job_id"])
    profile = repository.get_profile(conn)
    candidates = _candidates(profile, job, row)
    if not candidates:
        raise LLMOutputError("Add an employer or project to your profile first.")
    label = _label(_key(row["skill"]), job, row["requirement"], heading=False)    # as the posting writes it
    blocks = []
    for o in candidates:
        lines = "\n".join(f"  - {f'[{rid}] ' if rid else ''}{text}" for rid, text in o["lines"])
        blocks.append(f"{o['name']} ({o['kind']}; {o['role']}{'; ' + o['dates'] if o['dates'] else ''}):\n{lines}")
    prompt = (f"JOB: {job.title} at {job.company}\nTHE POSTING ASKS FOR: {row['requirement']}\nSKILL: {label}\n\n"
              "WHERE THE CANDIDATE WORKED (most relevant first), with what their records say they did:\n\n"
              + "\n\n".join(blocks))
    names = {o["name"].casefold(): o["name"] for o in candidates}
    note = ""
    for _ in range(2):
        answer = metrics.call(llm, "technical gap draft", _DRAFT_SYSTEM, prompt + note, _DRAFT_SCHEMA)
        text = " ".join(str(answer.get("text") or "").split()).strip()
        owner = names.get(str(answer.get("owner") or "").casefold().strip(), candidates[0]["name"])
        problems = _draft_problems(text, row["skill"], label)
        if not problems:
            why = " ".join(str(answer.get("why") or "").split())[:300]
            conn.execute("UPDATE gap_suggestions SET draft_owner=?, draft_text=?, draft_note=? WHERE id=?",
                         (owner, text, why or None, suggestion_id))
            conn.commit()
            return dict(conn.execute("SELECT * FROM gap_suggestions WHERE id=?", (suggestion_id,)).fetchone())
        note = f"\n\nYOUR LAST ANSWER: {text}\nFIX: {' '.join(problems)}"
    raise LLMOutputError(f"The AI could not write a usable line for {label}. Try again, or write it yourself.")
