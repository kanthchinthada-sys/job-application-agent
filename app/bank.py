"""Import a numbered experience bank ("AB01: ...", "XY01 — Northwind: ...") into the profile.

Works for anyone's bank; nothing here knows any particular person's employers.

* Each record is attached to the employer or project it belongs to, in this order:
  1. an explicit tag in the record: "XY01 — Northwind: ..." (matched against your experience and projects),
  2. the section it sits under: a line starting with an employer or project name ("Northwind Bank | Toronto ..."),
  3. where most records with the same prefix sit (so records pasted under the wrong employer are moved back),
  4. your only employer, when you have just one.
* Duplicates are removed (same ID twice, or the same text twice).
* Category labels ("Performance engineering") and editorial notes ("For AB24–AB28, retain the benchmark
  context...") are kept as metadata, never as resume bullets. Notes become a record's caveat.
* Gaps in numbering (e.g. XY01–XY06 missing) are reported, never filled in.
* Every record gets a status. Records are yours, so they start as "verified" unless the line is marked
  "(unverified)", "(partly true)" or "(not true)". Only "verified" records reach a resume. A status you
  change yourself is kept when you import again.
"""

import re
from collections import Counter

from pydantic import BaseModel

from app.schemas import BankRecord, Profile, RecordStatus

_RECORD = re.compile(r"^[\s•·*\-–]*([A-Z]{2,5})(\d{2,3})\s*(?:[—–-]\s*([^:]{1,40}?))?\s*:\s*(.+?)\s*$")
_NOTE = re.compile(r"^[\s•·*\-–]*(?:for\s+)?([A-Z]{2,5}\d{2,3})(?:\s*[–-]\s*([A-Z]{2,5}\d{2,3}))?\b[\s,]+(.*)$",
                   re.IGNORECASE)
_BULLET = re.compile(r"^[\s•·*\-–]+")
_MARKER = re.compile(r"\s*[\(\[]\s*(unverified|not verified|partly true|partially true|not true|false|draft)\s*[\)\]]\s*",
                     re.IGNORECASE)
_MARKER_STATUS: dict[str, RecordStatus] = {
    "unverified": "unverified", "not verified": "unverified", "draft": "unverified",
    "partly true": "partly_true", "partially true": "partly_true", "not true": "not_true", "false": "not_true",
}
_GENERIC_WORDS = {"the", "inc", "ltd", "llc", "corp", "co", "company", "group", "international", "global",
                  "technologies", "technology", "solutions", "services", "systems", "software", "labs", "and"}


class ImportReport(BaseModel):
    imported: int
    by_owner: dict[str, int]
    duplicates_removed: list[str]
    rerouted: list[str]              # "XY10: Northwind Bank -> Side project"
    missing_ids: list[str]           # numbering gaps, reported and NOT filled in
    notes_kept_as_caveats: list[str]
    labels: list[str]
    status_counts: dict[str, int]
    skill_groups: list[dict] = []    # from a "CORE SKILLS" section, e.g. {"name": "Test automation", "skills": [...]}


class _Owner(BaseModel):
    name: str
    kind: str                        # "experience" | "project"
    aliases: list[str]               # lower-case forms that identify it at the start of a line or in a tag


def _aliases(name: str) -> list[str]:
    low = " ".join(name.lower().split())
    base = re.split(r"\s*[|(,]\s*", low)[0].strip()
    out = [low, base]
    words = [w for w in re.findall(r"[a-z0-9&.+]+", base) if w not in _GENERIC_WORDS]
    if words and len(words[0]) >= 3:
        out.append(words[0])                         # "northwind" for "Northwind Bank"
    if len(words) >= 2:
        out.append(" ".join(words[:2]))
    return list(dict.fromkeys(a for a in out if a))


def _owners(profile: Profile) -> list[_Owner]:
    owners = [_Owner(name=e.company, kind="experience", aliases=_aliases(e.company)) for e in profile.experience]
    owners += [_Owner(name=p.name, kind="project", aliases=_aliases(p.name)) for p in profile.projects]
    return owners


def _match_owner(text: str, owners: list[_Owner], at_start: bool) -> _Owner | None:
    """The owner named in `text` (at its start, or anywhere for a record tag). Longest alias wins."""
    low = " ".join(text.lower().split())
    best, best_len = None, 0
    for o in owners:
        for a in o.aliases:
            pattern = rf"^{re.escape(a)}(?![a-z0-9])" if at_start else rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])"
            if len(a) > best_len and re.search(pattern, low):
                best, best_len = o, len(a)
    return best


def _tag_owner(tag: str, owners: list[_Owner]) -> _Owner | None:
    """ "XY01 — Northwind: ..." -> Northwind Bank; "— Projects:" -> your (first) project."""
    if tag.strip().lower() in ("project", "projects", "personal project", "personal projects"):
        return next((o for o in owners if o.kind == "project"), None)
    return _match_owner(tag, owners, at_start=False)


def _range(a: str, b: str | None) -> list[str]:
    if not b:
        return [a.upper()]
    pa, na = re.match(r"([A-Z]+)(\d+)", a.upper()).groups()
    pb, nb = re.match(r"([A-Z]+)(\d+)", b.upper()).groups()
    if pa != pb:
        return [a.upper(), b.upper()]
    width = len(na)
    return [f"{pa}{n:0{width}d}" for n in range(int(na), int(nb) + 1)]


def parse_bank(text: str, profile: Profile) -> tuple[list[BankRecord], ImportReport]:
    owners = _owners(profile)
    by_name = {o.name: o for o in owners}
    raw: list[dict] = []                 # records in bank order, before their owner is settled
    labels: list[str] = []
    caveats: dict[str, list[str]] = {}
    section: _Owner | None = None
    in_skills = False
    skill_groups: list[dict] = []
    group: str | None = None
    group_prefix: str | None = None      # a label belongs to the run of records that follows it (same prefix)

    for line_raw in text.splitlines():
        line = line_raw.strip()
        if not line:
            continue
        # "CORE SKILLS" section: "Test automation: Playwright, Selenium WebDriver, ..." lines become skill groups
        if line.isupper() and len(line) < 60:
            in_skills = "SKILL" in line
            if in_skills:
                continue
        if in_skills:
            m_sk = re.match(r"^([^:]{2,60}):\s*(.+)$", _BULLET.sub("", line))
            if m_sk:
                items = [x.strip(" .") for x in re.split(r",|;|\s+and\s+(?=[^,]*$)", m_sk.group(2)) if x.strip(" .")]
                items = [x for x in dict.fromkeys(items) if len(x) <= 80][:60]
                if items:
                    skill_groups.append({"name": m_sk.group(1).strip(), "skills": items})
            continue
        m = _RECORD.match(line)
        if m and not re.match(r"^[\s•·*\-–]*(for|the)\b", line, re.IGNORECASE):
            prefix, num, tag, body = m.groups()
            status, note = "verified", None
            mk = _MARKER.search(body)
            if mk:
                status = _MARKER_STATUS[mk.group(1).lower()]
                note = f"Marked “{mk.group(1)}” in your bank."
                body = _MARKER.sub(" ", body).strip()
            if group and group_prefix is None:
                group_prefix = prefix
            if group and prefix != group_prefix:
                group = None
            raw.append({"id": f"{prefix}{num}", "prefix": prefix, "body": body, "status": status, "note": note,
                        "tag": _tag_owner(tag, owners) if tag else None,
                        "section": section, "group": group})
            continue
        # a note that refers to records ("For AB24–AB28, ...", "AB57 describes ...")
        n = _NOTE.match(line)
        if n and len(line.split()) >= 6:
            for rid in _range(n.group(1), n.group(2)):
                caveats.setdefault(rid, []).append(_BULLET.sub("", line))
            continue
        # a section heading: an employer or project line ("Northwind Bank | Toronto, ON ...") or a category label
        hit = _match_owner(_BULLET.sub("", line), owners, at_start=True)
        if hit and (len(line) <= 120):
            section, group, group_prefix = hit, None, None
        elif line.lower().startswith("projects") and any(o.kind == "project" for o in owners):
            section = next(o for o in owners if o.kind == "project")
            group, group_prefix = None, None
        elif line.isupper() or any(line.strip() == e.title for e in profile.experience):
            continue                                   # "PROFESSIONAL EXPERIENCE", a job title
        elif len(line) <= 70 and not line.endswith(".") and ":" not in line and "|" not in line:
            label = _BULLET.sub("", line).strip()
            if label:
                group, group_prefix = label, None
                labels.append(label)

    # where most records of each prefix sit (only clear majorities count)
    majority: dict[str, _Owner] = {}
    counts: dict[str, Counter] = {}
    for r in raw:
        if r["section"] is not None and r["tag"] is None:
            counts.setdefault(r["prefix"], Counter())[r["section"].name] += 1
    for prefix, c in counts.items():
        name, n = c.most_common(1)[0]
        if n / sum(c.values()) >= 0.6 and n >= 2:
            majority[prefix] = by_name[name]
    only = [o for o in owners if o.kind == "experience"]

    records: dict[str, BankRecord] = {}
    seen_text: dict[str, str] = {}
    duplicates: list[str] = []
    rerouted: list[str] = []
    for r in raw:
        owner = r["tag"] or majority.get(r["prefix"]) or r["section"] or (only[0] if len(only) == 1 else None)
        rid = r["id"]
        if owner is None:
            labels.append(f"{rid}: no employer or project to attach it to, skipped (add the employer to your "
                          f"profile, or put the record under its employer's heading)")
            continue
        if rid in records or seen_text.get(r["body"].lower()) is not None:
            duplicates.append(rid if rid in records else f"{rid} (same text as {seen_text[r['body'].lower()]})")
            continue
        moved = r["section"] is not None and r["section"].name != owner.name
        if moved:
            rerouted.append(f"{rid}: {r['section'].name} -> {owner.name}")
        records[rid] = BankRecord(id=rid, text=r["body"], owner=owner.name, owner_kind=owner.kind,
                                  group=None if moved else r["group"], status=r["status"], status_note=r["note"])
        seen_text[r["body"].lower()] = rid

    for rid, notes in caveats.items():
        if rid in records:
            records[rid].caveat = " ".join(notes)

    ordered = list(records.values())
    by_owner: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for rec in ordered:
        by_owner[rec.owner] = by_owner.get(rec.owner, 0) + 1
        status_counts[rec.status] = status_counts.get(rec.status, 0) + 1
    report = ImportReport(
        imported=len(ordered), by_owner=by_owner, duplicates_removed=duplicates, rerouted=rerouted,
        missing_ids=_gaps(list(records)), notes_kept_as_caveats=sorted(caveats), labels=labels,
        status_counts=status_counts, skill_groups=skill_groups,
    )
    return ordered, report


def _gaps(ids: list[str]) -> list[str]:
    """Numbering gaps per prefix, e.g. FDE01-FDE06 when the bank starts at FDE07. Reported, never filled."""
    by_prefix: dict[str, list[int]] = {}
    width: dict[str, int] = {}
    for rid in ids:
        p, n = re.match(r"([A-Z]+)(\d+)", rid).groups()
        by_prefix.setdefault(p, []).append(int(n))
        width[p] = len(n)
    missing = []
    for p, nums in by_prefix.items():
        for n in range(1, max(nums) + 1):
            if n not in nums:
                missing.append(f"{p}{n:0{width[p]}d}")
    return missing


def merge(existing: list[BankRecord], new: list[BankRecord]) -> list[BankRecord]:
    """New text replaces old, but the status of a record you already had (yours to decide) is kept."""
    old = {r.id: r for r in existing}
    out = []
    for r in new:
        prev = old.pop(r.id, None)
        if prev is not None:
            r = r.model_copy(update={"status": prev.status, "status_note": prev.status_note})
        out.append(r)
    return out + list(old.values())


def verified(profile: Profile) -> list[BankRecord]:
    return [r for r in profile.experience_bank if r.status == "verified"]


def bank_profile(profile: Profile) -> Profile:
    """The profile with every VERIFIED bank record as a bullet of its employer or project (bank order).

    Used by the skill match, cover letters and the AI second look, so they see all your verified experience,
    not only the short highlights. Without verified records the profile is returned unchanged.
    """
    records = verified(profile)
    if not records:
        return profile
    exps = [e.model_copy(update={"highlights": [r.text for r in records if r.owner == e.company] or e.highlights})
            for e in profile.experience]
    projects = [p.model_copy(update={"highlights": [r.text for r in records if r.owner == p.name] or p.highlights})
                for p in profile.projects]
    return profile.model_copy(update={"experience": exps, "projects": projects})
