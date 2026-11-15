"""Skill comparison ("evidence_match_v2"): rule-based, explainable, no AI.

For each skill the job asks for, it looks for evidence in your profile (see app/evidence.py):
* **matched**: the same skill appears in your profile (skills, experience, projects or education);
* **related**: your profile shows it in other words, through a visible table of related terms
  (e.g. ETL work for "data pipelines", Jenkins for "CI/CD"). Each one shows the profile text, so check it;
* **missing**: nothing in your profile supports it.

Combined requirements are split: "Python/Java/Scala/go" counts as matched with Python, Java and Go, and lists
Scala as not found. Coverage = (matched + related) / total. It measures evidence of skills, NOT job fit.
"""

from app import evidence as ev
from app.schemas import Evidence, Profile, SkillResult, SkillSection

METHOD = ev.METHOD
METHOD_LABEL = (
    "Rule-based evidence comparison: same skill, or related experience from a visible table of related terms. "
    "Not an AI assessment."
)

LIMITATIONS = [
    "Rule-based: it finds words and a fixed list of related terms in your profile; it does not judge depth.",
    "'Related' matches (≈) are reasonable but not certain: check the profile text shown for each one.",
    "Skill depth, recency and years of experience are not assessed.",
    "Anything you have done but not written in your profile cannot be found. Add it to your profile.",
    "Coverage measures evidence of the listed skills, not suitability or hiring likelihood.",
]

# Kept for other modules (ATS, tailoring) that normalise skill names the same way.
ALIASES = ev.ALIASES
normalize = ev.normalize


def compare_skills(profile: Profile, skills: list[str]) -> SkillSection:
    from app.bank import bank_profile
    profile = bank_profile(profile)              # all verified experience-bank records count as evidence
    texts = ev.profile_texts(profile)
    vocab = ev.vocabulary(profile)
    matched, related, missing = [], [], []
    for skill in skills:
        m = ev.match_requirement(profile, skill, texts, vocab)
        if not m.found:
            missing.append(skill)
            continue
        items = [Evidence(kind="related" if e.related else "declared", source=e.source, text=e.text,
                          via=e.via, part=e.part) for e in m.evidence]
        result = SkillResult(skill=skill, normalized=normalize(skill), evidence=items,
                             found_parts=m.found, missing_parts=m.not_found)
        (related if all(e.related for e in m.evidence) else matched).append(result)
    total = len(skills)
    coverage = round((len(matched) + len(related)) / total * 100, 1) if total else None
    return SkillSection(total=total, matched=matched, related=related, mentioned_only=[], missing=missing,
                        coverage_percent=coverage)
