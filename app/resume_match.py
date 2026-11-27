"""Explain the technical coverage of a single résumé and surface verified evidence for it."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app import ats, bank, resume_render, tailoring, technical_requirements
from app.errors import ConflictError
from app.schemas import BankRecord, JobOut, Profile


_ON_RESUME = {"bullet", "skills", "mention"}
Placement = Literal["bullet", "skills", "mention", "verified", "profile", "transferable", "gap"]


class EvidenceOption(BaseModel):
    record_id: str
    owner: str
    text: str


class Requirement(BaseModel):
    keyword: str
    required: bool
    placement: Placement
    source: str = ""
    evidence: str | None = None
    owner: str | None = None
    options: list[EvidenceOption] = []
    needs_bank_evidence: bool = False


class Counts(BaseModel):
    total: int
    covered: int
    demonstrated: int


class Coverage(BaseModel):
    score: float | None
    required: Counts
    preferred: Counts
    rows: list[Requirement]


def _terms(keyword: str) -> tuple[str, ...]:
    return technical_requirements.options(keyword)


def _mentions(text: str, terms: tuple[str, ...], *, wording_only: bool = False) -> bool:
    return (technical_requirements.mentions if wording_only else technical_requirements.supports)(text, terms)


def _destination(resume: tailoring.TailoredResume, record: BankRecord):
    owner = record.owner.casefold()
    if record.owner_kind == "experience":
        roles = [r for r in resume.experience if r.company.casefold() == owner]
        return roles[0] if len(roles) == 1 else None
    projects = [p for p in resume.projects if p["name"].casefold() == owner]
    return projects[0] if len(projects) == 1 else None


def _options(resume, records: list[BankRecord], terms: tuple[str, ...]) -> list[EvidenceOption]:
    choices = []
    for record in records:
        if record.caveat or not _destination(resume, record) or not _mentions(record.text, terms):
            continue
        choices.append(EvidenceOption(record_id=record.id, owner=record.owner, text=record.text))
        if len(choices) == 3:
            break
    return choices


def _counts(rows: list[Requirement], required: bool) -> Counts:
    group = [row for row in rows if row.required == required]
    return Counts(total=len(group), covered=sum(r.placement in _ON_RESUME for r in group),
                  demonstrated=sum(r.placement == "bullet" for r in group))


def analyze(resume: tailoring.TailoredResume, profile: Profile, job: JobOut) -> Coverage:
    bullets = [(b.text, role.company) for role in resume.experience for b in role.bullets]
    bullets += [(text, project["name"]) for project in resume.projects
                for text in (project.get("highlights") or resume_render._sentences(project.get("description") or ""))]
    skills = [(skill, "Core Skills") for skill in
              ([s for group in resume.skill_groups for s in group["skills"]] if resume.skill_groups else resume.skills)]
    skills += [(skill, project["name"]) for project in resume.projects for skill in project.get("skills", [])]
    mentions = [(text, "Professional Summary") for text in resume_render._sentences(resume.summary)]
    mentions += [(label, "Certifications and Professional Development") for label in resume.certifications]
    mentions += [(label, "Achievements") for label in resume.achievements]
    records = bank.verified(profile)
    profile_text = tailoring.profile_text(profile)
    rows = []
    for item in technical_requirements.for_job(job):
        terms = item.options
        row = Requirement(keyword=item.label, required=item.required, placement="gap", source=item.source)
        for placement, lines in (("bullet", bullets), ("skills", skills), ("mention", mentions)):
            hit = next(((line, owner) for line, owner in lines if _mentions(line, terms)), None)
            if hit:
                row.placement = placement
                row.evidence, row.owner = hit
                break
        if row.placement != "bullet":
            row.options = _options(resume, records, terms)
        if row.placement == "gap":
            if row.options:
                row.placement = "verified"
            elif proof := technical_requirements.profile_evidence(profile, terms):
                row.evidence = proof
                row.placement = "profile"
            elif related := next((r for r in records if technical_requirements.relates(r.text, terms)), None):
                row.evidence, row.owner, row.placement = related.text, related.owner, "transferable"
            elif proof := next((found for term in terms if (found := ats.profile_evidence(profile_text, term, profile))
                                and "(related:" in found), None):
                row.evidence = proof
                row.placement = "transferable"
        row.needs_bank_evidence = row.placement in {"gap", "transferable"} or (
            row.placement == "profile" and not any(_mentions(record.text, terms) for record in records))
        rows.append(row)
    required, preferred = _counts(rows, True), _counts(rows, False)
    weight = 2 * required.total + preferred.total
    score = round(100 * (2 * required.covered + preferred.covered) / weight, 1) if weight else None
    return Coverage(score=score, required=required, preferred=preferred, rows=rows)


def ats_report(coverage: Coverage) -> ats.ATSReport:
    matched, evidenced, gaps = [], [], []
    for row in coverage.rows:
        keyword = ats.Keyword(keyword=row.keyword, weight=2 if row.required else 1, evidence=row.evidence)
        if row.placement in _ON_RESUME:
            matched.append(keyword)
        elif row.placement in {"verified", "profile"}:
            evidenced.append(keyword)
        else:
            gaps.append(keyword)
    total = 2 * coverage.required.total + coverage.preferred.total
    best = round(100 * sum(k.weight for k in matched + evidenced) / total, 1) if total else 0.0
    return ats.ATSReport(method="technical_resume_coverage", compared=len(coverage.rows), score=coverage.score or 0.0,
                         max_honest_score=best, matched=matched, missing_but_evidenced=evidenced, gaps=gaps,
                         note="Coverage of the posting’s technical skills. Required terms count twice.")


@dataclass
class PlacementEdit:
    previous: str | None
    record: BankRecord


def apply_record(resume: tailoring.TailoredResume, profile: Profile, keyword: str, record_id: str,
                 before: Coverage) -> PlacementEdit:
    row = next((r for r in before.rows if r.keyword == keyword), None)
    if row is None or not any(option.record_id == record_id for option in row.options):
        raise ConflictError("That verified record is no longer available for this requirement. Refresh the review.")
    record = next(r for r in bank.verified(profile) if r.id == record_id)
    target = _destination(resume, record)
    previous = None
    if isinstance(target, tailoring.Role):
        for index, bullet in enumerate(target.bullets):
            source_ids = set(bullet.source_ids) | ({bullet.source_id} if bullet.source_id else set())
            if source_ids == {record.id} or (not source_ids and record.text == bullet.original):
                previous = target.bullets.pop(index).text
                break
        target.bullets.insert(0, tailoring.Bullet(text=record.text, original=record.text, changed=False,
                                                  source_id=record.id, source_ids=[record.id]))
    else:
        assert isinstance(target, dict)  # analyze offered a single matching destination
        highlights = list(target.get("highlights") or resume_render._sentences(target.get("description") or ""))
        source_ids = list(target.get("highlight_ids", []))[:len(highlights)]
        source_ids += [None] * (len(highlights) - len(source_ids))
        if record.id in source_ids:
            index = source_ids.index(record.id)
            previous = highlights.pop(index)
            source_ids.pop(index)
        target.update(highlights=[record.text, *highlights], highlight_ids=[record.id, *source_ids])
    return PlacementEdit(previous=previous, record=record)
