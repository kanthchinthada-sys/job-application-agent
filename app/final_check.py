"""The last check of a resume and a cover letter before they are handed back.

It reads the finished documents the way a recruiter and an applicant tracking system will:

Resume
- employers, official titles and dates exactly as in your profile, in reverse chronological order;
- no placeholder or unfinished text, no merged bullets (a bullet is one sentence), no bullet or summary sentence
  said twice, each skill once and in the line of its own kind;
- the first third: the strongest evidence for the posting's first priority is the first bullet of its role (moved
  up when it is buried), and the summary leads with it;
- the export: text taken from the printed page keeps the order of headings, employers, dates and bullets.

Cover letter
- the exact role and company in the opening; no placeholder, unfinished or repeated text; every "At X, I" names
  one of your employers.

Fixes it can make safely are made (moving the strongest bullet up, removing a duplicate or a placeholder); the rest
is reported. The report goes to the private review.
"""

import html as _html
import re

_HEADINGS = ["Professional Summary", "Core Skills", "Professional Experience", "Projects", "Education"]
_DANGLING = re.compile(r"\b(and|or|the|a|an|of|to|with|for|by|in|on|at|from|into|while|that|which|using)\.?$",
                       re.IGNORECASE)
_PLACEHOLDER = re.compile(r"write something|lorem ipsum|\btodo\b|\btbd\b|placeholder|\[.*?\]|\{.*?\}", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _unfinished(text: str) -> bool:
    t = text.strip()
    return bool(t) and (not t.endswith((".", "!", "?", ")")) or bool(_DANGLING.search(t.rstrip(".").strip())))


def page_text(rendered_html: str) -> str:
    """The text a reader (or a parser) gets from the printed page, in reading order."""
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", rendered_html)
    body = re.sub(r"(?i)<br\s*/?>|</(p|li|h1|h2|div|tr)>", "\n", body)
    return _html.unescape(re.sub(r"<[^>]+>", " ", body))


def check_resume(resume, profile, job, priorities: list[dict] | None = None,
                 render=None, recent_ai_ids=()) -> dict:
    """Check (and where safe, fix) a tailored resume in place. Returns {problems, fixed, first_third}."""
    from app import bank, core_skills
    problems: list[str] = []
    fixed: list[str] = []

    # --- employers, titles, dates, order ------------------------------------------------------------------------
    own = [(e.company, e.title, e.start_date, e.end_date) for e in profile.experience]
    shown = [(r.company, r.title, r.start_date, r.end_date) for r in resume.experience]
    for s in shown:
        if s not in own:
            problems.append(f"Employer, title or dates differ from your profile: {s[0]} | {s[1]}")
    order = [own.index(s) for s in shown if s in own]
    if order != sorted(order):
        problems.append("Roles are not in the order of your profile (reverse chronological).")

    # --- bullets: placeholders, merged, unfinished, duplicates ---------------------------------------------------
    seen: dict[str, str] = {}
    for role in resume.experience:
        keep = []
        for b in role.bullets:
            text = b.text.strip()
            if _PLACEHOLDER.search(text) or bank._PLACEHOLDER_TEXT.search(text):   # (a short bullet is not one)
                fixed.append(f"Removed placeholder text from {role.company}: \"{text}\"")
                continue
            if len(re.findall(r"[.!?](?:\s+[A-Z])", text)) >= 1:
                first = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text, maxsplit=1)[0]
                fixed.append(f"Kept one sentence of a merged bullet at {role.company}: \"{text[:70]}...\"")
                b.text = text = first
            if _unfinished(text):
                problems.append(f"Unfinished bullet at {role.company}: \"{text}\"")
            key = _norm(text)
            if key in seen:
                fixed.append(f"Removed a bullet said twice ({role.company} and {seen[key]}): \"{text[:70]}\"")
                continue
            seen[key] = role.company
            keep.append(b)
        role.bullets = keep

    # --- summary ----------------------------------------------------------------------------------------------
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", resume.summary.strip()) if x]
    unique, said = [], set()
    for x in sentences:
        if _PLACEHOLDER.search(x):
            fixed.append(f"Removed placeholder text from the summary: \"{x}\"")
            continue
        if _norm(x) in said:
            fixed.append(f"Removed a summary sentence said twice: \"{x[:70]}\"")
            continue
        if _unfinished(x) or len(x.split()) < 3:
            problems.append(f"Unfinished summary sentence: \"{x}\"")
        said.add(_norm(x))
        unique.append(x)
    resume.summary = " ".join(unique)

    # --- skills: each once, in the line of its own kind -------------------------------------------------------
    labels = {c for c, _ in core_skills.CATEGORIES}
    core = resume.skill_groups and all(any(g["name"].startswith(lab) for lab in labels) for g in resume.skill_groups)
    listed: set[str] = set()
    for g in resume.skill_groups:
        keep = []
        for s in g["skills"]:
            if s.lower() in listed:
                fixed.append(f"Removed \"{s}\" listed twice in Core Skills")
                continue
            if core and not core_skills.is_listable(s):
                fixed.append(f"Removed \"{s}\" from Core Skills (a posting phrase, not a skill name)")
                continue
            home = core_skills.category_of(s) if core else None
            fits = (not home or g["name"].startswith(home)
                    or (home == "API testing" and g["name"].startswith("Test automation"))
                    or (home in ("Kubernetes and deployment", "Cloud and messaging") and g["name"].startswith("CI/CD"))
                    or (home == "Data validation" and s.lower() == "sql" and g["name"] == "Languages"))
            if not fits:
                fixed.append(f"Removed \"{s}\" from \"{g['name']}\" (it is not that kind of skill)")
                continue
            listed.add(s.lower())
            keep.append(s)
        g["skills"] = keep
    resume.skill_groups = [g for g in resume.skill_groups if g["skills"]]

    # --- the first third: the strongest evidence for the first priority near the top ---------------------------
    first_third = {}
    lead = next((p for p in (priorities or []) if p.get("main") and p.get("records")), None)
    if lead:
        rid = lead["records"][0]
        for ri, role in enumerate(resume.experience):
            ids = [b.source_id for b in role.bullets]
            if rid in ids:
                pos = ids.index(rid)
                pinned = ri == 0 and bool(set(recent_ai_ids).intersection(
                    role.bullets[0].source_ids or [ids[0]]))
                if pos > 0 and not pinned:
                    role.bullets.insert(0, role.bullets.pop(pos))
                    fixed.append(f"Moved the strongest evidence for \"{lead['ask'][:60]}\" ({rid}) to the top of "
                                 f"{role.company}")
                    pos = 0
                first_third = {"priority": lead["ask"], "record": rid, "where": f"{role.company}, bullet {pos + 1}"}
                break
        else:
            first_third = {"priority": lead["ask"], "record": rid, "where": "summary only (not a bullet on this page)"}

    # --- the export keeps the reading order -------------------------------------------------------------------
    if render is not None:
        text = page_text(render(resume))
        low = text.lower()
        pos, last = [], -1
        for h in _HEADINGS:
            # the heading on a line of its own: "in personal projects" in the summary is not the Projects heading
            m = re.search(rf"(?m)^\s*{re.escape(h.lower())}\s*$", low)
            i = m.start() if m else -1
            if i >= 0:
                if i < last:
                    problems.append(f"In the exported text, \"{h}\" comes out of order.")
                last = i
        cursor = low.find("professional experience")
        for role in resume.experience:
            i = low.find(role.company.lower(), max(cursor, 0))
            if i < 0:
                problems.append(f"In the exported text, {role.company} is missing or out of order.")
                continue
            cursor = i
            for b in role.bullets[:3]:
                j = _norm(text[cursor:]).find(_norm(b.text)[:60])
                if j < 0:
                    problems.append(f"In the exported text, a {role.company} bullet is missing or out of order.")
                    break
    return {"problems": problems, "fixed": fixed, "first_third": first_third}


def check_letter(body: str, profile, job) -> dict:
    """Check a cover letter body. Returns {problems}."""
    from app.claims import employer_forms
    problems: list[str] = []
    paras = [p for p in body.split("\n\n") if p.strip()]
    opening = next((p for p in paras if not re.match(r"^(Dear|Hello|Hi)\b", p)), "")
    if job.company.lower() not in opening.lower():
        problems.append(f"The opening does not name {job.company}.")
    if _norm(job.title) not in _norm(opening):
        problems.append(f"The opening does not name the exact role ({job.title}).")
    sentences = [x for p in paras for x in re.split(r"(?<=[.!?])\s+", p) if x.strip()]
    said = set()
    for x in sentences:
        if _PLACEHOLDER.search(x):
            problems.append(f"Placeholder text: \"{x}\"")
        if re.match(r"^(Dear|Sincerely|Best regards|Regards)\b", x) or len(x.split()) < 3:
            continue                     # (a fragment of a name like "Example Inc. (fictional)" is not a sentence)
        if _norm(x) in said:
            problems.append(f"Said twice: \"{x[:70]}\"")
        said.add(_norm(x))
        if _unfinished(x) and not x.rstrip().endswith(","):
            problems.append(f"Unfinished sentence: \"{x}\"")
    names = {f.lower() for f in employer_forms(profile)}
    for m in re.finditer(r"\bAt ([A-Z][\w&.' -]+?), I\b", body):
        if m.group(1).lower() not in names:
            problems.append(f"\"At {m.group(1)}\" is not one of your employers as your profile names them.")
    return {"problems": problems}
