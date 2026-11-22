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

METHOD = "ats_keywords_v2"   # v2: technical keywords only; every match shows the resume line

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
    # quality reporting you describe in your own words counts as "quality metrics"
    "quality metrics": ["defect escape", "regression stability", "test metrics", "quality metrics",
                        "quality reporting", "coverage gaps"],
    "test metrics": ["defect escape", "regression stability", "test metrics"],
}


class Keyword(BaseModel):
    keyword: str
    weight: int                   # 2 = required, 1 = preferred
    evidence: str | None = None   # profile text that supports it


class ATSReport(BaseModel):
    method: str
    compared: int = 0             # how many of the job's keywords were compared (0: the job lists no skills)
    score: float                  # % of weighted job keywords found literally in the resume
    max_honest_score: float       # score if every evidenced keyword were added
    matched: list[Keyword]
    missing_but_evidenced: list[Keyword]
    gaps: list[Keyword]           # nothing in the profile supports these
    not_scored: list[str] = []    # people skills, ways of working, hiring words: not an ATS keyword (v2)
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
    """ "Python/Java/Scala/go", "Playwright or Selenium", "Jenkins, GitHub Actions, or GitLab CI" -> the options
    (any one of them meets the requirement). Not for fixed pairs like CI/CD, and not for "and/or" lists."""
    k = keyword.strip()
    low = k.lower()
    if re.search(r"\b(ci/cd|a/b|tcp/ip|i/o|ui/ux|24/7|and/or)\b", low):
        k = re.sub(r"(?i)\b(ci/cd|a/b|tcp/ip|i/o|ui/ux|24/7)\b", lambda m: m.group(0).replace("/", "\0"), k)
    if "/" not in k and not re.search(r"\bor\b", k, re.IGNORECASE):
        return []
    alts = [a.strip(" ,.").replace("\0", "/") for a in re.split(r"/|,?\s+or\s+|,", k, flags=re.IGNORECASE)]
    alts = [a for a in alts if a]
    return alts if len(alts) > 1 and all(len(a) > 1 and len(a.split()) <= 4 for a in alts) else []


_SKILL_FILLER = re.compile(r"^(strong|solid|advanced|good|basic|hands-on|proven)\s+|"
                           r"\s+(skills?|experience|knowledge|proficiency|expertise|background)$", re.IGNORECASE)


def skill_terms(keyword: str) -> list[str]:
    """The skill name(s) a keyword stands for, as they could be listed on a resume: "SQL skills" -> ["SQL"],
    "Playwright or Selenium" -> ["Playwright", "Selenium"]. Never the either-or phrase itself."""
    out = []
    for term in _alternatives(keyword) or [keyword.strip()]:
        prev = None
        while prev != term:
            prev, term = term, _SKILL_FILLER.sub("", term).strip()
        if term and term not in out:
            out.append(term)
    return out


def is_named_tool(term: str) -> bool:
    """A specific product, language or platform ("AWS", "Playwright"), not a practice ("API testing")."""
    from app.vocabulary import TOOLS, canonical
    from app.selection import KNOWN_TECH
    names = ({t.lower() for t in TOOLS} | {t.lower() for t in KNOWN_TECH}) - _PRACTICES
    return term.lower() in names or canonical(term).lower() in names


# listed with the tools, but practices that equivalent work shows (Jenkins pipelines are CI/CD)
_PRACTICES = {"ci/cd", "rest apis", "rest api", "microservices", "graphql", "websockets", "html", "css"}


def supports_skill(profile_text: str, term: str, profile=None) -> str | None:
    """The profile text that lets this skill be LISTED on the resume, or None.

    A named tool needs the tool itself (or a spelling of it) in your profile: related experience is never enough
    (Kubernetes does not show AWS, Selenium does not show Playwright). A practice ("API testing") may also be shown
    by the equivalent work listed in EQUIVALENTS (e.g. REST Assured tests). An either-or phrase ("Playwright or
    Selenium") is never a skill you can list: list the option you have."""
    if len(skill_terms(term)) > 1 or skill_terms(term) != [term.strip()]:
        return None
    if is_named_tool(term):
        phrases = list(_forms(term)) + [alias for alias, target in ALIASES.items() if target == normalize(term)]
        texts = [profile_text]
        if profile is not None:
            texts += [r.text for r in getattr(profile, "experience_bank", []) if r.status == "verified"]
        for text in texts:
            for sentence in re.split(r"(?<=[.!?;])\s+|\n", text):
                s = " ".join(sentence.lower().split())
                if any(_contains(s, p) for p in phrases):
                    return sentence.strip()
        return None
    found = profile_evidence(profile_text, term, profile)
    return found if found and "(related:" not in found else None


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
    if m := re.fullmatch(r"(.+?)\s*\((.+)\)", keyword.strip()):     # "version control (Git)": either names it
        return resume_has(resume_text, m.group(1), profile) or resume_has(resume_text, m.group(2), profile)
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
    # your verified experience-bank records that didn't make this resume still count as your own words
    records = [r.text for r in getattr(profile, "experience_bank", []) if r.status == "verified"]
    if records and (found := find_evidence("\n".join(records), keyword)):
        return found
    m = ev.match_requirement(bank_profile(profile), keyword)
    if not m.evidence:
        return None
    best = sorted(m.evidence, key=lambda e: (e.related, -len(e.via)))[0]   # same word first, then the most specific
    return best.text + (f" (related: {best.via})" if best.related else "")


def resume_line(resume_text: str, keyword: str) -> str | None:
    """The resume line where an ATS would find the keyword (or one of its alternatives)."""
    lines = [x.strip() for x in resume_text.splitlines() if x.strip()]
    for kw in [keyword] + _alternatives(keyword):
        for line in lines:
            if keyword_in(line, kw):
                for sentence in re.split(r"(?<=[.!?])\s+", line):     # the sentence, not a whole role
                    if keyword_in(sentence, kw):
                        return sentence[:220]
    return None


def analyze(resume_text: str, profile_text: str, required: list[str], preferred: list[str],
            exclude: set[str] = frozenset(), profile=None) -> ATSReport:
    from app.vocabulary import is_technical
    keywords = [(k, 2) for k in required] + [(k, 1) for k in preferred if k not in required]
    matched, evidenced, gaps, not_scored = [], [], [], []
    for kw, weight in keywords:
        if kw.lower() in exclude or kw.strip().lower() in ("untitled job",):    # job-title words / placeholders
            continue
        if not is_technical(kw):
            not_scored.append(kw)
            continue
        if resume_has(resume_text, kw, profile):
            matched.append(Keyword(keyword=kw, weight=weight, evidence=resume_line(resume_text, kw)))
            continue
        evidence = profile_evidence(profile_text, kw, profile)
        (evidenced if evidence else gaps).append(Keyword(keyword=kw, weight=weight, evidence=evidence))
    total = sum(k.weight for k in matched + evidenced + gaps) or 1
    score = round(100 * sum(k.weight for k in matched) / total, 1)
    best = round(100 * sum(k.weight for k in matched + evidenced) / total, 1)
    compared = len(matched + evidenced + gaps)
    note = ("Technical keywords only (how most ATS filters work); people skills and ways of working are left "
            "out. Gaps can only be closed with real experience, e.g. a small project you actually build.")
    if compared == 0:
        note = ("This job lists no skills to compare (often only a short excerpt of the posting was available). "
                "Paste the full job description, then tailor the resume again.")
    return ATSReport(
        method=METHOD, compared=compared, score=score, max_honest_score=best, matched=matched,
        missing_but_evidenced=evidenced, gaps=gaps, not_scored=list(dict.fromkeys(not_scored)), note=note,
    )
