"""Interview prep from the saved posting, submitted document versions and verified records."""

from app import repository, selection, tailoring


def build(conn, application_id: int) -> dict:
    application = repository.get_application(conn, application_id)
    profile = repository.get_profile(conn)
    job = repository.get_job(conn, application.job_id)
    docs = repository.list_documents(conn, application_id)
    versions = {}
    for kind, document_id in (("resume", application.submitted_resume_id),
                              ("cover_letter", application.submitted_cover_letter_id)):
        doc = next((d for d in docs if d.kind == kind and d.id == document_id), None)
        if doc is None:
            doc = next((d for d in docs if d.kind == kind), None)
        if doc:
            versions[kind] = {"id": doc.id, "version": doc.version, "submitted": doc.id == document_id}

    resume_ids = set()
    if "resume" in versions:
        draft = repository.get_document(conn, versions["resume"]["id"])
        resume = tailoring.TailoredResume.model_validate_json(draft.content)
        resume_ids = {b.source_id for role in resume.experience for b in role.bullets if b.source_id}
    letter_ids = set()
    if "cover_letter" in versions:
        draft = repository.get_document(conn, versions["cover_letter"]["id"])
        for check in draft.review.get("claim_checks", []):
            if isinstance(check, dict):
                letter_ids.update(r["id"] for r in check.get("records", []) if isinstance(r, dict) and "id" in r)
    plan = selection.plan(profile, job)
    records = {r.id: r for r in profile.experience_bank if r.status == "verified"}
    questions, gaps, used = [], [], set()
    for row in plan.rows:
        if row.category != "technical" or row.kind == "preferred":
            continue
        candidates = [records[rid] for rid in row.selected_ids if rid in records and rid not in used]
        candidates.sort(key=lambda r: (r.id not in resume_ids, r.id not in letter_ids))
        if candidates and len(questions) < 6:
            r = candidates[0]
            used.add(r.id)
            questions.append({"topic": row.requirement, "question": "Walk me through a specific example of this work.",
                              "owner": r.owner, "record_id": r.id, "evidence": r.text, "caveat": r.caveat,
                              "on_resume": r.id in resume_ids, "in_letter": r.id in letter_ids})
        if row.not_found_parts and row.strength in ("gap", "partial", "transferable"):
            gaps.extend(row.not_found_parts)
    return {"versions": versions, "questions": questions, "topics_to_review": list(dict.fromkeys(gaps))[:12]}
