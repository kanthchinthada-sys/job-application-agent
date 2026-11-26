"""Scoped document edits and their evidence review."""

import re

from app import bank, claims, drafting, final_check, tailoring
from app.errors import ConflictError


def _paragraphs(content: str) -> list[str]:
    return re.split(r"\n\s*\n", content.strip())


def change(doc, section: str, text: str, profile, job, role_index: int | None, item_index: int | None):
    if doc.kind == "cover_letter" and section == "paragraph" and item_index is not None:
        paras = _paragraphs(doc.content)
        if not 0 <= item_index < len(paras) or len(_paragraphs(text)) != 1:
            raise ConflictError("Choose one paragraph from the cover letter.")
        previous = paras[item_index]
        paras[item_index] = text
        content = "\n\n".join(paras)
        facts = drafting.build_facts(profile)
        notes = []
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            chosen = drafting.support_facts(sentence, facts, profile)
            notes.extend(issue["detail"] for issue in claims.check(
                sentence, [f.text for f in chosen], profile, job, tool_owners=True))
        notes += final_check.check_letter(content, profile, job)["problems"]
        return previous, content, notes, []

    if doc.kind != "resume":
        raise ConflictError("This document has no editable section of that type.")
    resume = tailoring.TailoredResume.model_validate_json(doc.content)
    evidence = []
    notes = []
    if section == "summary":
        previous = resume.summary
        resume.summary = text
        resume.summary_source = "user_edit"
        facts = drafting.build_facts(profile)
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            chosen = drafting.support_facts(sentence, facts, profile)
            notes.extend(issue["detail"] for issue in claims.check(
                sentence, [f.text for f in chosen], profile, job, context=[profile.summary or ""], tool_owners="named"))
    elif section == "skills":
        previous = "\n".join(f"{g['name']}: {', '.join(g['skills'])}" for g in resume.skill_groups) \
            or "Skills: " + ", ".join(resume.skills)
        grouped = {}
        for line in text.splitlines():
            label, sep, items = line.partition(":")
            values = [item.strip() for item in items.split(",") if item.strip()]
            if not sep or not label.strip() or not values:
                raise ConflictError("Use one group per line, in the format Group: skill, skill.")
            grouped.setdefault(label.strip(), []).extend(values)
        resume.skill_groups = [{"name": label, "skills": list(dict.fromkeys(values))}
                               for label, values in grouped.items()]
        resume.skills = list(dict.fromkeys(s for group in resume.skill_groups for s in group["skills"]))
        checked = resume.model_copy(deep=True)
        notes.extend(f"Your profile does not support {skill}." for skill in
                     tailoring.drop_unsupported_skills(checked, profile))
    elif section == "bullet" and role_index is not None and item_index is not None:
        if not 0 <= role_index < len(resume.experience):
            raise ConflictError("Choose a role from this resume version.")
        role = resume.experience[role_index]
        if not 0 <= item_index < len(role.bullets):
            raise ConflictError("Choose a bullet from this resume version.")
        bullet = role.bullets[item_index]
        previous = bullet.text
        ids = set(bullet.source_ids) | {bullet.source_id}
        evidence = [r for r in bank.verified(profile) if r.id in ids and r.owner == role.company]
        support = [r.text for r in evidence] or [bullet.original]
        notes.extend(issue["detail"] for issue in claims.check(text, support, profile, job))
        if any(r.caveat for r in evidence):
            notes.extend(f"Keep {r.id}'s original qualification: {r.caveat}" for r in evidence if r.caveat)
        bullet.text, bullet.changed = text, text != bullet.original
    else:
        raise ConflictError("Choose a resume summary, Core Skills, or an experience bullet.")
    checked = resume.model_copy(deep=True)
    quality = final_check.check_resume(checked, profile, job)
    notes.extend(quality["problems"] + quality["fixed"])
    return previous, resume.model_dump_json(), list(dict.fromkeys(notes)), evidence
