"""ATS keyword-match analysis ("ats_keywords_v1").

Applicant tracking systems mostly check whether the posting's keywords appear,
literally, in the resume. This module:

1. scores a resume against the job's keywords (required count double),
2. finds keywords the resume lacks but your PROFILE evidences, often in
   different words (e.g. the job says "UAT", your resume says "business
   acceptance testing"), so they can honestly be added in the job's wording,
3. lists true gaps: keywords nothing in your profile supports.

Every "evidenced" keyword carries the profile text that backs it, so you can
check it. The equivalence table is deliberately small and explicit.
"""

import re

from pydantic import BaseModel

from app.matching import ALIASES, normalize

METHOD = "ats_keywords_v1"

# job keyword (normalised) -> phrases in a profile that honestly evidence it
EQUIVALENTS: dict[str, list[str]] = {
    "uat": ["user acceptance testing", "business acceptance testing", "uat"],
    "uat testing": ["user acceptance testing", "business acceptance testing", "uat"],
    "user acceptance testing": ["user acceptance testing", "business acceptance testing", "uat"],
    "ci/cd": ["jenkins", "azure devops", "github actions", "ci/cd"],
    "ci/cd pipelines": ["jenkins", "azure devops", "github actions", "ci/cd"],
    "continuous integration": ["jenkins", "azure devops", "github actions"],
    "generative ai": ["github copilot", "claude code", "ai-generated"],
    "ai enabled qa": ["github copilot", "claude code", "ai-generated tests"],
    "ai-assisted testing": ["github copilot", "claude code"],
    "test strategy": ["test planning", "testing priorities"],
    "test planning": ["test planning", "test plans"],
    "requirement traceability": ["requirements traceability"],
    "requirements traceability": ["requirements traceability"],
    "release readiness": ["view of readiness", "release validation", "release decisions"],
    "communication": ["stakeholder communication", "stakeholders"],
    "stakeholder management": ["stakeholder communication", "stakeholders"],
    "test management": ["jira", "test plans", "test cases"],
    "test automation": ["automation"],
    "regression testing": ["regression"],
    "functional testing": ["functional and regression testing", "functional testing"],
    "api testing": ["api tests", "api testing", "rest assured", "postman", "api responses"],
    "integration testing": ["integration testing", "integration automation", "integration coverage"],
    "test data": ["test data"],
    "test stability": ["timing failures", "targeted retries"],
    "flaky tests": ["timing failures", "targeted retries"],
    "defect triage": ["defect triage"],
    "defect management": ["defect triage", "defects"],
    "mentoring": ["mentored", "mentoring"],
    "load testing": ["k6", "sustained load"],
    "performance testing": ["k6", "p95/p99 latency"],
    "bdd": ["cucumber", "bdd"],
}


class Keyword(BaseModel):
    keyword: str
    weight: int                   # 2 = required, 1 = preferred
    evidence: str | None = None   # profile text that supports it


class ATSReport(BaseModel):
    method: str
    score: float                  # % of weighted job keywords found literally in the resume
    max_honest_score: float       # score if every evidenced keyword were added
    matched: list[Keyword]
    missing_but_evidenced: list[Keyword]
    gaps: list[Keyword]           # nothing in the profile supports these
    note: str


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(phrase.lower())}(?![\w])", text) is not None


def _forms(keyword: str) -> set[str]:
    """Literal spellings ATS would accept for this keyword."""
    k = keyword.lower().strip()
    forms = {k, normalize(keyword)}
    if k in ("net", ".net"):
        forms |= {".net", "net"}
    return {f for f in forms if f}


def keyword_in(text: str, keyword: str) -> bool:
    t = " ".join(text.lower().split())
    return any(_contains(t, f) for f in _forms(keyword))


def find_evidence(profile_text: str, keyword: str) -> str | None:
    """Return the profile sentence that evidences the keyword (directly or via EQUIVALENTS)."""
    phrases = list(_forms(keyword)) + EQUIVALENTS.get(normalize(keyword), []) + EQUIVALENTS.get(keyword.lower(), [])
    phrases += [alias for alias, target in ALIASES.items() if target == normalize(keyword)]  # e.g. postgres
    for sentence in re.split(r"(?<=[.!?;])\s+|\n", profile_text):
        s = " ".join(sentence.lower().split())
        if any(_contains(s, p) for p in phrases):
            return sentence.strip()
    return None


def _alternatives(keyword: str) -> list[str]:
    """ "Python/Java/Scala/go" -> its alternatives. Not for fixed pairs like CI/CD or names with spaces."""
    k = keyword.strip()
    if "/" not in k or " " in k or k.lower() in ("ci/cd", "a/b", "tcp/ip", "i/o"):
        return []
    alts = [a.strip() for a in k.split("/") if a.strip()]
    return alts if len(alts) > 1 and all(len(a) > 1 for a in alts) else []


def resume_has(resume_text: str, keyword: str, profile=None) -> bool:
    """Would an ATS find this keyword in the resume?

    The literal keyword; for a list of alternatives like "Python/Java/Scala/go", any of them; or the same words in
    another form ("Automated test frameworks" ~ "automation frameworks", "Quality gate" ~ "quality gates").
    Related-but-different words ("ETL" for "data pipelines") do NOT count here: an ATS would not see them.
    """
    if keyword_in(resume_text, keyword):
        return True
    if any(keyword_in(resume_text, a) for a in _alternatives(keyword)):
        return True
    from app import evidence as ev
    words = ev._content_words(keyword)
    return len(words) > 1 and ev._all_words_present(keyword.lower(), " ".join(resume_text.lower().split()))


def profile_evidence(profile_text: str, keyword: str, profile=None) -> str | None:
    """Profile text backing the keyword: literal, the EQUIVALENTS table, or the evidence rules (app/evidence.py).

    Evidence found through related words is labelled, e.g. "(related: release readiness)", so you can judge it.
    """
    found = find_evidence(profile_text, keyword)
    if found or profile is None:
        return found
    from app import evidence as ev
    from app.bank import bank_profile
    m = ev.match_requirement(bank_profile(profile), keyword)
    if not m.evidence:
        return None
    best = sorted(m.evidence, key=lambda e: (e.related, -len(e.via)))[0]   # same word first, then the most specific
    return best.text + (f" (related: {best.via})" if best.related else "")


def analyze(resume_text: str, profile_text: str, required: list[str], preferred: list[str],
            exclude: set[str] = frozenset(), profile=None) -> ATSReport:
    keywords = [(k, 2) for k in required] + [(k, 1) for k in preferred if k not in required]
    matched, evidenced, gaps = [], [], []
    for kw, weight in keywords:
        if kw.lower() in exclude:
            continue
        if resume_has(resume_text, kw, profile):
            matched.append(Keyword(keyword=kw, weight=weight))
            continue
        evidence = profile_evidence(profile_text, kw, profile)
        (evidenced if evidence else gaps).append(Keyword(keyword=kw, weight=weight, evidence=evidence))
    total = sum(k.weight for k in matched + evidenced + gaps) or 1
    score = round(100 * sum(k.weight for k in matched) / total, 1)
    best = round(100 * sum(k.weight for k in matched + evidenced) / total, 1)
    return ATSReport(
        method=METHOD, score=score, max_honest_score=best, matched=matched,
        missing_but_evidenced=evidenced, gaps=gaps,
        note="Keyword match only (how most ATS filters work). Gaps can only be closed with real experience, "
             "e.g. a small project you actually build.",
    )
