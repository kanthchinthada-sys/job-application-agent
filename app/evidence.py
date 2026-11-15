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
}


def normalize(skill: str) -> str:
    key = " ".join(skill.lower().split()).rstrip(".")
    return ALIASES.get(key, key)



# requirement term (normalised) -> words/phrases in a profile that show it. Edit freely; keep every entry defensible.
RELATED: dict[str, list[str]] = {
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
    stems = {_stem(t) for t in tokens}
    return all((w in tokens) or (len(w) > 6 and _stem(w) in stems) or (w + "s") in tokens or
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
    """The skills a requirement names: its short parts, plus known skill names inside a long sentence."""
    parts = []
    for p in split_parts(requirement):
        words = p.split()
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
    # drop a part already covered by a longer one ("cloud" inside "cloud and container technologies" stays: both
    # are useful); only exact duplicates are removed above.
    return parts


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
            for src, txt, low in lowered:
                form = next((f for f in _forms(part) if _has(low, f)), None)
                if form:
                    hit = Evidence(part=part, via=form, source=src, text=txt, related=False)
                    break
        if hit is None:
            for src, txt, low in lowered:
                via = next((r for r in RELATED.get(part, []) if _has(low, r)), None)
                if via:
                    hit = Evidence(part=part, via=via.strip(), source=src, text=txt, related=True)
                    break
        if hit is None and len(_content_words(part)) >= 2:
            # Same words, different form: "FHIR payload validation" ~ "Validated FHIR payloads".
            for src, txt, low in lowered:
                if _all_words_present(part, low):
                    hit = Evidence(part=part, via=part, source=src, text=txt, related=False)
                    break
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

_REQUIRED_HEAD = re.compile(r"^\s*(required|minimum|basic)?\s*(qualifications|requirements|what you('ll)? need|"
                            r"what we('re)? looking for|you have|must have)\b.*:?\s*$", re.IGNORECASE)
_PREFERRED_HEAD = re.compile(r"^\s*(preferred|desired|nice to have|bonus|good to have|additional)\b.*:?\s*$",
                             re.IGNORECASE)
_OTHER_HEAD = re.compile(r"^\s*(responsibilities|what you('ll)? do|about|benefits|why|the role|compensation)\b",
                         re.IGNORECASE)
_SECTION_END = re.compile(r"^\s*(at [\w&.]+,? we|please note|the estimated|in addition, this role|we offer|"
                          r"equal opportunity|this role is eligible)\b", re.IGNORECASE)


_RESP_HEAD = re.compile(r"^\s*(responsibilities|key responsibilities|what you('ll)? do|in this role|the role|"
                        r"your impact|day to day)\b.*:?\s*$", re.IGNORECASE)


def posting_responsibilities(description: str) -> list[str]:
    """The 'Responsibilities' lines of a posting (empty if it has no such section)."""
    out: list[str] = []
    collecting = False
    for raw in description.splitlines():
        line = raw.strip(" \t•*-–·")
        if not line:
            continue
        if _RESP_HEAD.match(line) and len(line) < 60:
            collecting = True
            continue
        if collecting and ((_REQUIRED_HEAD.match(line) or _PREFERRED_HEAD.match(line) or _OTHER_HEAD.match(line))
                           and len(line) < 60 or _SECTION_END.match(line)):
            break
        if collecting and 3 <= len(line.split()) <= 60:
            out.append(line)
    return out


def posting_requirements(description: str) -> tuple[list[str], list[str]]:
    """The 'Required' and 'Preferred' qualification lines of a posting (empty if it has no such sections)."""
    required: list[str] = []
    preferred: list[str] = []
    current: list[str] | None = None
    for raw in description.splitlines():
        line = raw.strip(" \t•*-–·")
        if not line:
            continue
        if _PREFERRED_HEAD.match(line) and len(line) < 60:
            current = preferred
            continue
        if _REQUIRED_HEAD.match(line) and len(line) < 60:
            current = required
            continue
        if (_OTHER_HEAD.match(line) and len(line) < 80) or _SECTION_END.match(line):
            current = None
            continue
        if current is not None and 1 <= len(line.split()) <= 60:
            current.append(line)
    return required, preferred
