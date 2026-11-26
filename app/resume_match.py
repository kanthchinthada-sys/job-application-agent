"""Explain the technical coverage of a single résumé and surface verified evidence for it."""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from app import ats, bank, evidence, resume_render, tailoring, vocabulary
from app.errors import ConflictError
from app.schemas import BankRecord, JobOut, Profile


_SAME_SKILL = {"user acceptance testing": "uat", "uat testing": "uat",
               "continuous integration/continuous delivery": "ci/cd"}
_AMBIGUOUS_ALIASES = {"node", "rest"}
_NEGATED_WORK = re.compile(r"\b(?:did not|didn['’]t|never|without|no hands-on experience)\s+"
                           r"(?:use|implement|build|test|work with|experience with)?", re.IGNORECASE)
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
    evidence: str | None = None
    owner: str | None = None
    options: list[EvidenceOption] = []


class Counts(BaseModel):
    total: int
    covered: int
    demonstrated: int


class Coverage(BaseModel):
    score: float | None
    required: Counts
    preferred: Counts
    rows: list[Requirement]


def _canonical(term: str) -> str:
    norm = evidence.normalize(term)
    return _SAME_SKILL.get(norm, norm)


def _terms(keyword: str) -> tuple[str, ...]:
    alternatives = re.sub(r"\band\s*/\s*or\b", "or", keyword, flags=re.IGNORECASE)
    return tuple(sorted({_canonical(term) for term in ats.skill_terms(alternatives) if vocabulary.is_technical(term)}))


def _technical_requirements(job: JobOut):
    seen = set()
    for required, skills in ((True, job.required_skills), (False, job.preferred_skills)):
        for keyword in skills:
            if not vocabulary.is_technical(keyword) or keyword.casefold().strip() == job.title.casefold().strip():
                continue
            terms = _terms(keyword)
            if terms and terms not in seen:
                seen.add(terms)
                yield keyword, required, terms


@lru_cache(maxsize=512)
def _term_forms(term: str) -> tuple[tuple[str, ...], bool]:
    variants = (term, *(a for a, target in evidence.ALIASES.items() if target == term and a not in _AMBIGUOUS_ALIASES),
                *(a for a, target in _SAME_SKILL.items() if target == term))
    return variants, ats.is_named_tool(term)


def _mentions(text: str, terms: tuple[str, ...], *, wording_only: bool = False) -> bool:
    for term in terms:
        if vocabulary.canonical(term) in vocabulary.CASE_SENSITIVE:
            names = {name for _, _, name in vocabulary.find_terms(text)}
            if (vocabulary.canonical(term) in names or (term == "c" and text.strip() == "C")
                    or (term == "go" and re.search(r"\b(?:in|using|with) Go\b", text))):
                return True
            continue
        variants, named = _term_forms(term)
        if any(ats.keyword_in(text, variant) for variant in variants):
            return True
        if not wording_only and not named and ats.resume_has(text, term):
            return True
    return False


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
        if (record.caveat or not _destination(resume, record) or not _mentions(record.text, terms)
                or any(_mentions(record.text[hit.end():].split(".", 1)[0][:60], terms, wording_only=True)
                       for hit in _NEGATED_WORK.finditer(record.text))):
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
    for keyword, required, terms in _technical_requirements(job):
        row = Requirement(keyword=keyword, required=required, placement="gap")
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
            elif proof := next((found for term in terms if (found := ats.profile_evidence(profile_text, term, profile))), None):
                row.evidence = proof
                row.placement = "profile" if _mentions(proof.split(" (related:", 1)[0], terms) else "transferable"
        rows.append(row)
    required, preferred = _counts(rows, True), _counts(rows, False)
    weight = 2 * required.total + preferred.total
    score = round(100 * (2 * required.covered + preferred.covered) / weight, 1) if weight else None
    return Coverage(score=score, required=required, preferred=preferred, rows=rows)


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
