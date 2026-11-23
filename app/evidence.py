"""Find the evidence in YOUR profile for each thing a job asks for, even when you used different words.

Used by the skill match and by cover letters (to pick the experience that fits the job's requirements).

Rules, all visible here so every match can be checked:
1. A requirement is split into parts: "Python/Java/Scala/go" -> Python, Java, Scala, Go (any one is enough);
   "API testing tools (e.g., Postman or similar)" -> API testing, Postman.
2. Each part is looked up literally, then through RELATED: a table of terms that honestly show the same skill
   (e.g. "data pipelines" is shown by ETL work; "cloud" by AWS, Azure or Kubernetes).
3. Degrees are checked against your education.
Every match returns the exact profile text it found, so you can see why it counted.
"""

import functools
import re

from pydantic import BaseModel

from app.schemas import Profile

METHOD = "evidence_match_v2"

# Small and explicit on purpose: every entry is a claim we can defend.
ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "node": "node.js",
    "nodejs": "node.js",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "golang": "go",
    "amazon web services": "aws",
    "cicd": "ci/cd",
    "ci-cd": "ci/cd",
    "rest": "rest apis",
    "rest api": "rest apis",
    "restful apis": "rest apis",
    # the same skill written without a space or as an abbreviation ("RestAssured", "E2E", "OOP")
    "restassured": "rest assured",
    "rest-assured": "rest assured",
    "e2e": "end-to-end testing",
    "e2e testing": "end-to-end testing",
    "end to end testing": "end-to-end testing",
    "end-to-end tests": "end-to-end testing",
    "oop": "oop principles",
    "object-oriented programming": "oop principles",
    "object oriented programming": "oop principles",
    "object-oriented design": "oop principles",
    "model context protocol": "mcp",
    "sdet roles": "sdet",
    "senior sdet": "sdet",
    "cd pipeline": "ci/cd pipelines",
    "cd pipelines": "ci/cd pipelines",
    "net": ".net",
    "net ecosystem": ".net",
    "elt processes": "elt",
    "elt pipelines": "elt",
    "software quality engineering": "quality engineering",
    "devops ci": "ci/cd",
    "ai-assisted workflows": "ai-assisted development",
}


def normalize(skill: str) -> str:
    key = " ".join(skill.lower().split()).rstrip(".")
    return ALIASES.get(key, key)



# requirement term (normalised) -> words/phrases in a profile that show it. Edit freely; keep every entry defensible.
RELATED: dict[str, list[str]] = {
    # multi-tenant cloud services are SaaS-like, but not the same: shown as related, never added to your skills
    "quality metrics": ["defect escape", "regression stability", "quality reporting", "test metrics", "coverage gaps"],
    "go-no-go": ["go/no-go", "go-no-go", "release readiness", "release risks", "release decisions", "sign-off"],
    "release readiness": ["release readiness", "release risks", "go/no-go", "release decisions", "release verification"],
    "saas": ["cross-tenant", "multi-tenant", "tenant isolation", "cloud-service", "cloud services"],
    # mobile / release / AI-assisted work (from real postings)
    "ios": ["appium", "mobile automation", "mobile app", "xcuitest", "ios"],
    "android": ["appium", "mobile automation", "mobile app", "espresso", "android"],
    "mobile testing": ["appium", "mobile automation", "mobile"],
    "release automation": ["release regression", "release validation", "pipeline", "jenkins", "github actions", "ci/cd"],
    "agentic workflows": ["claude code", "copilot", "agent", "agents", "agentic", "ai-assisted", "ai-generated"],
    "ai agents": ["agent", "agents", "agentic", "llm", "language model"],
    "claude": ["claude", "claude code"],
    "root cause analysis": ["root cause", "investigated", "isolate", "diagnos", "correlating", "distinguishing"],
    "debugging": ["debug", "investigated", "troubleshoot", "diagnos"],
    "troubleshooting": ["troubleshoot", "investigated", "debug", "diagnos"],
    "acceptance criteria": ["acceptance", "requirements", "testable scenarios", "test conditions"],
    "release cycles": ["release", "regression", "ci/cd", "pipeline"],
    "version control": ["git", "github", "bitbucket", "svn"],
    "test plans": ["test plans", "test planning", "test strategy", "test strategies"],
    "test planning": ["test plans", "test planning", "test strategy", "test strategies"],
    "collaboration": ["collaborat", "partnered", "worked with", "stakeholders", "cross-functional"],
    "communication": ["communicat", "presented", "stakeholders", "reported", "documented"],
    "ownership": ["owned", "end-to-end", "led"],
    "llm": ["llm", "language model", "ollama", "ai-generated", "generated content"],
    "evaluation frameworks": ["evaluation", "evaluate", "prompt-regression", "metamorphic", "adversarial"],
    "observability": ["observability", "monitoring", "opensearch", "prometheus", "grafana", "logs", "metrics"],
    "alerting": ["alert", "monitoring", "prometheus", "grafana"],
    # engineering practice
    "api testing": ["api testing", "api tests", "api automation", "rest assured", "postman", "tested apis", "api responses"],
    "api": ["api", "apis", "rest assured", "postman"],
    "rest apis": ["rest assured", "postman", "api"],
    "microservices": ["microservices", "distributed", "services"],
    "backend systems": ["api", "microservices", "services", "backend", "database"],
    "distributed systems": ["distributed", "microservices", "kubernetes"],
    "automated test frameworks": ["test frameworks", "automation framework", "reusable java automation", "junit",
                                  "testng", "cucumber", "pytest", "rest assured", "shared test utilities"],
    "test frameworks": ["test frameworks", "automation framework", "junit", "testng", "cucumber", "pytest"],
    "test automation": ["automation", "automated"],
    "automation": ["automation", "automated"],
    "test scripts": ["automation", "tests", "test suite"],
    "regression testing": ["regression"],
    "integration testing": ["integration testing", "integration automation", "integration coverage"],
    "functional testing": ["functional", "functional and regression testing"],
    "performance testing": ["k6", "latency", "throughput", "load"],
    "load testing": ["k6", "sustained load", "bursts", "load"],
    "performance and load testing tools": ["k6"],
    "ci/cd": ["jenkins", "azure devops", "github actions", "ci/cd", "gradle builds", "maven"],
    "ci/cd pipelines": ["jenkins", "azure devops", "github actions", "ci/cd"],
    "continuous integration": ["jenkins", "azure devops", "github actions"],
    "quality gates": ["release validation", "release decisions", "release readiness", "deployment verification"],
    "release validation": ["release validation", "release decisions", "readiness", "deployment verification"],
    "test data": ["test data", "datasets"],
    "synthetic test data": ["test data"],
    "sdlc": ["release", "requirements", "test plans", "deployment"],
    "software development lifecycle": ["release", "requirements", "test plans", "deployment"],
    "debugging": ["investigate", "investigated", "debug", "debugged", "trace", "root cause"],
    "debug": ["investigate", "investigated", "debug", "debugged", "trace", "root cause"],
    "test failures": ["failures", "failed", "investigated", "failure investigation"],
    "failures": ["failures", "failed", "investigated"],
    "root cause": ["root cause", "root causes", "investigated", "isolate"],
    "root-cause analysis": ["root cause", "root causes", "investigated", "isolate"],
    "performance": ["latency", "throughput", "k6", "performance", "benchmark", "benchmarks"],
    "test execution": ["execution", "jenkins", "regression runs"],
    "deployment reliability": ["readiness", "upgrade", "rollout"],
    "data reconciliation": ["reconciliation", "source-to-target"],
    "problem-solving": ["investigate", "debug", "trace", "distinguish product defects", "resolve defects"],
    "problem solving": ["investigate", "debug", "trace", "distinguish product defects", "resolve defects"],
    "communication": ["stakeholder", "stakeholders", "communication", "clients", "mentored"],
    "customer-facing": ["customer", "customers", "client", "clients", "stakeholders", "business stakeholders"],
    "customers": ["customer", "customers", "client", "clients"],
    "stakeholders": ["stakeholder", "stakeholders", "business analysts", "customer", "customers", "clients"],
    "deployment": ["deployment", "deployments", "deploy", "rollout", "upgrade", "upgrades"],
    "deploy": ["deployment", "deployments", "deploy", "rollout", "upgrade"],
    "integrations": ["integration", "integrations", "integrated", "apis"],
    "stakeholder management": ["stakeholder", "stakeholders"],
    "mentoring": ["mentored", "mentoring"],
    "leadership": ["led ", "mentored", "coordinated"],
    "test strategy": ["test planning", "testing priorities", "test plans", "automation strategy"],
    "defect management": ["defect triage", "defects"],
    "uat": ["user acceptance testing", "business acceptance testing", "uat"],
    "generative ai": ["github copilot", "claude code", "ai-generated"],
    "ai-assisted testing": ["github copilot", "claude code", "ai-generated"],
    "ai-assisted development": ["github copilot", "claude code", "ai-assisted", "ai-generated"],
    # platforms and data
    "cloud": ["aws", "azure", "gcp", "kubernetes", "cloud"],
    "cloud technologies": ["aws", "azure", "gcp", "kubernetes", "cloud"],
    "container technologies": ["kubernetes", "docker", "helm", "pod", "container"],
    "containers": ["kubernetes", "docker", "helm", "pod", "container"],
    "cloud and container technologies": ["aws", "azure", "gcp", "kubernetes", "docker", "helm", "cloud"],
    "kubernetes": ["kubernetes", "helm", "pod"],
    "databases": ["sql", "mysql", "oracle", "postgresql", "database", "opensearch"],
    "database": ["sql", "mysql", "oracle", "postgresql", "database"],
    "backend validation": ["sql", "database", "data reconciliation", "api responses"],
    "data pipelines": ["etl", "data-processing", "data processing", "transformation", "source-to-target",
                       "data pipeline"],
    "data validation": ["etl validation", "data quality", "reconciliation"],
    "data quality": ["data quality", "data-quality", "reconciliation"],
    "etl": ["etl"],
    "monitoring": ["prometheus", "grafana", "opensearch", "datadog", "monitoring"],
    "observability": ["prometheus", "grafana", "opensearch", "datadog", "application logs", "observability"],
    "monitoring and observability tools": ["prometheus", "grafana", "opensearch", "datadog"],
    "service health": ["readiness", "service health", "availability"],
    "agile": ["agile", "scrum", "sprint", "kanban"],
    # skills a posting names that your work shows without the same words: OOP is how Java and Page Object
    # frameworks are written; Playwright/Selenium suites are end-to-end tests; GitHub/Bitbucket are Git
    "oop principles": ["object-oriented", "oop", "page object", "java", "c#", "kotlin", "typescript"],
    "end-to-end testing": ["end-to-end", "e2e", "playwright", "selenium", "cypress", "webdriverio"],
    # building web applications (not testing them): an application you built with a web front end or API
    "web applications": ["web application", "web app", "frontend", "front end", "fastapi", "angular", "react",
                         "spring boot"],
    "prompt engineering": ["prompt engineering", "prompt", "prompts", "prompt-regression"],
    "mcp": ["mcp", "model context protocol"],
    "git": ["git", "github", "gitlab", "bitbucket"],
    "code reviews": ["code review", "code reviews", "pull request", "pull requests"],
    "rest assured": ["rest assured"],
    # your role itself: an SDET / QA career is shown by test automation and QA work
    "sdet": ["test automation", "automated test", "automation framework", "test developer"],
    "software qa": ["qa", "quality assurance", "quality engineering", "test automation", "test lead"],
    "quality engineering": ["quality engineering", "qa", "test automation"],
    "qa strategy": ["test strategy", "testing priorities", "automation strategy", "test plans"],
    "elt": ["etl", "elt"],
    "ai-enabled": ["claude code", "github copilot", "ai-assisted", "llm", "language model"],
    "software development": ["java", "python", "typescript", "fastapi", "spring boot", "developed"],
    "agile methodologies": ["agile", "scrum", "sprint", "kanban"],
}

# Words that only describe a requirement ("experience with", "tools") and carry no skill of their own.
_FILLER = re.compile(
    r"\b(hands-on|strong|solid|proven|excellent|good|working|deep|basic|familiarity|familiar|knowledge|understanding|"
    r"proficiency|proficient|experience|experienced|ability|skills?|with|in|of|for|using|the|a|an|and|as|is|etc|such as|fields?|"
    r"e\.g\.?|i\.e\.?|or similar|similar|related|tools?|minimum|\d+\+? years?|years?)\b",
    re.IGNORECASE,
)


class Evidence(BaseModel):
    part: str           # the part of the requirement that was found
    via: str            # the profile word that showed it ("python", or "etl" for "data pipelines")
    source: str         # where in the profile
    text: str           # the profile text itself
    related: bool       # True if found through the RELATED table rather than the same word


class RequirementMatch(BaseModel):
    requirement: str
    parts: list[str]
    found: list[str]            # parts with evidence
    not_found: list[str]
    evidence: list[Evidence]

    @property
    def matched(self) -> bool:
        return bool(self.found)


def split_parts(requirement: str) -> list[str]:
    """'Python/Java/Scala/go' -> ['python', 'java', 'scala', 'go']; keeps the whole phrase too when it has meaning."""
    text = requirement.replace("’", "'")
    examples = re.findall(r"\(([^)]*)\)", text)              # "(e.g., Postman or similar)"
    main = re.sub(r"\([^)]*\)", " ", text)
    parts: list[str] = []
    for chunk in [main] + examples:
        for piece in re.split(r"/|,|;|\bor\b|\band\b|&", chunk):
            cleaned = " ".join(_FILLER.sub(" ", piece).split()).strip(" .:-")
            if cleaned and len(cleaned) > 1 or cleaned.lower() in ("go", "r", "c"):
                parts.append(normalize(cleaned))
    whole = normalize(" ".join(_FILLER.sub(" ", main).split()).strip(" .:-"))
    if whole and whole not in parts and whole in RELATED:
        parts.insert(0, whole)
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return seen


def profile_texts(profile: Profile) -> list[tuple[str, str]]:
    """(source, text) for everything in the profile, most recent experience first."""
    out: list[tuple[str, str]] = []
    for exp in profile.experience:
        where = f"{exp.title} at {exp.company}"
        for h in exp.highlights:
            out.append((where, h))
        if exp.skills:
            out.append((f"skills used at {exp.company}", ", ".join(exp.skills)))
    for proj in profile.projects:
        out.append((f"project: {proj.name}", proj.description + (" " + ", ".join(proj.skills) if proj.skills else "")))
        for h in proj.highlights:
            out.append((f"project: {proj.name}", h))
    for group in profile.skill_groups:
        out.append((f"skills: {group.name}", ", ".join(group.skills)))
    if profile.skills:
        out.append(("profile skills", ", ".join(profile.skills)))
    for edu in profile.education:
        out.append(("education", f"{edu.degree}, {edu.institution}"))
    for cert in profile.certifications:
        out.append(("certifications", cert))
    for ach in profile.achievements:
        out.append(("achievements", ach))
    if profile.summary:
        out.append(("summary", profile.summary))
    return out


def _has(text: str, phrase: str) -> bool:
    phrase = phrase.lower()
    if phrase.endswith(" "):                                  # "led " = the word "led", not "ledger"
        return re.search(rf"(?<![\w]){re.escape(phrase.strip())}\b", text) is not None
    return re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", text) is not None


def _forms(part: str) -> list[str]:
    forms = {part}
    forms.update(alias for alias, target in ALIASES.items() if target == part)
    return sorted(forms, key=len, reverse=True)


_STOPWORDS = {"with", "and", "the", "for", "from", "into", "across", "using", "that", "this", "their", "your", "our"}


def _content_words(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#.]+", phrase.lower()) if len(w) > 2 and w not in _STOPWORDS]


def _stem(word: str) -> str:
    return word[:6] if len(word) > 6 else word


def _all_words_present(phrase: str, text: str) -> bool:
    """Every content word of the phrase appears in the text, allowing a different ending (payload/payloads)."""
    tokens = re.findall(r"[a-z0-9+#.]+", text)
    by_stem: dict[str, int] = {}
    for t in tokens:
        by_stem[_stem(t)] = max(by_stem.get(_stem(t), 0), len(t))

    def same_word(w: str) -> bool:
        # another ending of the same word (payload/payloads, automated/automation), not a shorter different word
        # ("access" is not "accessibility")
        return len(w) > 6 and by_stem.get(_stem(w), 0) >= len(w) - 3

    return all((w in tokens) or same_word(w) or (w + "s") in tokens or
               (w.endswith("s") and w[:-1] in tokens) for w in _content_words(phrase))


def _degree_part(part: str) -> str | None:
    for level in ("bachelor", "master", "phd", "doctorate", "diploma"):
        if level in part:
            return level
    return None


def vocabulary(profile: Profile) -> set[str]:
    """Skill names worth spotting inside a long requirement sentence."""
    words = {normalize(s) for s in profile.skills}
    words |= {normalize(s) for g in profile.skill_groups for s in g.skills}
    words |= {normalize(s) for e in profile.experience for s in e.skills}
    words |= set(RELATED) | set(ALIASES) | set(ALIASES.values())
    return {w for w in words if w}


def requirement_parts(requirement: str, vocab: set[str]) -> list[str]:
    """The skills a requirement names: its short parts, plus known skill names inside a long sentence.

    Matching a job means asking this for every (bullet, requirement) pair, so the answer is cached per
    requirement and vocabulary."""
    return list(_requirement_parts_cached(requirement, frozenset(vocab)))


@functools.lru_cache(maxsize=4096)
def _requirement_parts_cached(requirement: str, vocab: frozenset) -> tuple[str, ...]:
    return tuple(_requirement_parts(requirement, vocab))


def _requirement_parts(requirement: str, vocab) -> list[str]:
    # "go/no-go" is a release decision, not the Go language
    requirement = re.sub(r"\bgo\s*/\s*no[- ]go\b|\bno[- ]go\b", "go-no-go", requirement, flags=re.IGNORECASE)
    parts = []
    lead_words = {"to", "and", "or", "the", "a", "an", "with", "of", "for", "in", "on", "our", "your", "its", "be",
                  "strong", "proven", "solid", "good", "excellent", "deep", "hands-on", "ability", "able"}
    for p in split_parts(requirement):
        words = p.split()
        while words and words[0] in lead_words:            # "to mentor" -> "mentor"
            words = words[1:]
        # a leading -ing verb names the activity, not the skill: "assessing release readiness" -> "release readiness"
        while len(words) >= 2 and words[0].endswith("ing") and " ".join(words) not in RELATED \
                and words[0] not in vocab and words[0] not in RELATED:
            words = words[1:]
        p = " ".join(words)
        if not p or (len(words) == 1 and p.endswith("ing") and p not in vocab and p not in RELATED):
            continue                                       # "maintaining", "including": not a skill
        if "go-no-go" in p:
            p = "go-no-go"
        if len(words) >= 3 and p not in RELATED:
            # a long phrase: its meaningful words are the skills ("customer-facing", "deployment", ...)
            subs = [w for w in words if w in RELATED or w in vocab]
            parts.extend(subs or ([p] if len(words) <= 4 else []))
        elif len(words) <= 4:
            parts.append(p)
    low = " ".join(requirement.lower().replace("\u2019", "'").split())
    found = []
    for term in vocab:
        if len(term) <= 2:      # "go", "r": only when listed, e.g. "Python/Java/go" or "Python, go"
            pattern = rf"(?:^|[/,(]\s*){re.escape(term)}(?=\s*(?:[/,)]|$|\s+for\b))"
        else:
            pattern = rf"(?<![\w]){re.escape(term)}(?![\w])"
        m = re.search(pattern, low)
        if m:
            found.append((m.start(), term))
    for _, term in sorted(found):
        if term not in parts:
            parts.append(term)
    # the posting vocabulary's names for what the line asks ("mentor" -> "mentoring", "quality metrics")
    from app.vocabulary import find_terms
    for _, _, name in find_terms(requirement):
        name = name.lower()
        if name not in parts and (name in RELATED or name in vocab):
            parts.append(name)
    # drop a part already covered by a longer one ("cloud" inside "cloud and container technologies" stays: both
    # are useful); only exact duplicates are removed above.
    return parts


def _find_part(part: str, lowered: list[tuple[str, str, str]]) -> Evidence | None:
    """The first text that shows one part: the same words (or an alias), then a RELATED term, then the same
    content words in another form ("FHIR payload validation" ~ "Validated FHIR payloads")."""
    for src, txt, low in lowered:
        form = next((f for f in _forms(part) if _has(low, f)), None)
        if form:
            return Evidence(part=part, via=form, source=src, text=txt, related=False)
    for src, txt, low in lowered:
        # a related word inside a hyphenated name is not that word: "client" in "client-go" or
        # "Kubernetes-client" is a library, not a client of the business
        via = next((r for r in RELATED.get(part, [])
                    if re.search(rf"(?<![\w-]){re.escape(r.lower().strip())}(?![\w-])", low)), None)
        if via:
            return Evidence(part=part, via=via.strip(), source=src, text=txt, related=True)
    if len(_content_words(part)) >= 2:
        for src, txt, low in lowered:
            if _all_words_present(part, low):
                return Evidence(part=part, via=part, source=src, text=txt, related=False)
    return None


def find_part(part: str, texts: list[tuple[str, str]]) -> Evidence | None:
    """Evidence for one named skill in (source, text) pairs, or None."""
    return _find_part(normalize(part), [(src, txt, " ".join(txt.lower().split())) for src, txt in texts])


def match_requirement(profile: Profile, requirement: str, texts: list[tuple[str, str]] | None = None,
                      vocab: set[str] | None = None) -> RequirementMatch:
    texts = texts if texts is not None else profile_texts(profile)
    lowered = [(src, txt, " ".join(txt.lower().split())) for src, txt in texts]
    vocab = vocab if vocab is not None else vocabulary(profile)
    parts = requirement_parts(requirement, vocab) or [normalize(requirement)]
    evidence: list[Evidence] = []
    found: list[str] = []
    for part in parts:
        hit: Evidence | None = None
        degree = _degree_part(part)
        if degree:      # a degree is shown by your education, nothing else
            for src, txt, low in lowered:
                if (src == "education" or low.startswith("education:")) and (
                        degree in low or (degree == "phd" and "doctor" in low)):
                    hit = Evidence(part=part, via=degree, source="education", text=txt, related=False)
                    break
            if hit is None:
                continue
        if hit is None:
            hit = _find_part(part, lowered)
        if hit:
            found.append(part)
            evidence.append(hit)
    # Not found = named parts with no evidence, leaving out fragments of something that was found
    # ("ci" and "cd pipelines" when "ci/cd pipelines" was found).
    not_found = [p for p in parts if p not in found and not any(p in f or f in p for f in found)]
    return RequirementMatch(requirement=requirement, parts=parts, found=found, not_found=not_found, evidence=evidence)


# ---------------------------------------------------------------------------
# Requirements from the posting text
# ---------------------------------------------------------------------------

# Section headings, in the many ways postings write them ("What you need to be successful", "Who you are", ...).
_REQUIRED_HEAD = re.compile(
    # phrases that only ever start a heading ("What you need to be successful", "Who you are", ...)
    r"^\s*(?:(required|minimum|basic|key|core)\s+(qualifications|requirements|skills|experience)\b.*|"
    r"(qualifications|requirements)\b.*|what you('ll)? (need|bring)\b.*|what we('re)? looking for\b.*|"
    r"what we need\b.*|who you are\b.*|about you\b.*|you('ll)? (have|bring)\s*:?|you are\s*:?|"
    r"your (profile|skills|experience|background)\b.*|must[- ]haves?\b.*|(the )?ideal candidate\b.*|"
    r"what makes you\b.*|is this you\b.*|what it takes\b.*|to be successful\b.*|"
    r"you('ll)? thrive if\b.*|you might be a fit if\b.*|"
    # single words that are headings only when they stand alone ("Skills", "Experience and skills:")
    r"(skills|experience|expertise)( (and|&) (experience|skills|qualifications))?\s*:?)\s*$", re.IGNORECASE)
_PREFERRED_HEAD = re.compile(r"^\s*(preferred|desired|desirable|nice[- ]to[- ]have|bonus|good to have|additional|"
                             r"plus(es)?|extra credit|it('s| is| would be) (great|nice|a plus)|even better|ideally|"
                             r"brownie points)\b.*:?\s*$", re.IGNORECASE)
_OTHER_HEAD = re.compile(r"^\s*(responsibilities|key responsibilities|what you('ll)? (be )?do(ing)?|about|benefits|"
                         r"perks|why|the role|your role|compensation|what you('ll)? get|what we offer|our offer|"
                         r"pay range|salary|how to apply|our (hiring|interview) process|location|working at)\b",
                         re.IGNORECASE)
_SECTION_END = re.compile(r"^\s*(at [\w&.]+,? we|please note|the estimated|in addition, this role|we offer|"
                          r"equal opportunity|this role is eligible|in compliance with|pay range)\b", re.IGNORECASE)
_PREFERRED_LINE = re.compile(r"\b(nice to have|is a plus|a plus|are a plus|bonus|preferred|desirable|ideally)\b",
                             re.IGNORECASE)


_RESP_HEAD = re.compile(r"^\s*(responsibilities|key responsibilities|what you('ll)? (be )?do(ing)?|in this role|"
                        r"the role|your role|your impact|day to day|your mission|key accountabilities|"
                        r"what the job involves|you will)\b.*:?\s*$", re.IGNORECASE)


def _clean_line(raw: str) -> str:
    return raw.replace("\u2019", "'").replace("\u2018", "'").strip(" \t•*-–·")


def posting_responsibilities(description: str) -> list[str]:
    """The duty lines of a posting ("What you'll do", "Responsibilities", "Your impact", ...): app/posting.py."""
    from app import posting
    return [l for l in posting.requirement_lines(description)[2] if 3 <= len(l.split()) <= 60]


def posting_requirements(description: str) -> tuple[list[str], list[str]]:
    """The required and preferred lines of a posting, under any of the usual headings: app/posting.py."""
    from app import posting
    req, pref, _ = posting.requirement_lines(description)
    return [l for l in req if 1 <= len(l.split()) <= 60], [l for l in pref if 1 <= len(l.split()) <= 60]
