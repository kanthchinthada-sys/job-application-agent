"""Technical requirements shared by résumé scoring and Experience Bank review."""

import re
from dataclasses import dataclass
from functools import lru_cache

from app import ats, evidence, posting, vocabulary

_SAME_SKILL = {"user acceptance testing": "uat", "uat testing": "uat", "on call": "on-call",
               "continuous integration/continuous delivery": "ci/cd"}
_AMBIGUOUS_ALIASES = {"node", "rest"}
_BOILERPLATE = re.compile(r"^(?:about (?!you\b|the role\b|this role\b)|perks (?:and|&) benefits|"
                          r"(?:our|company) benefits|what we offer|why join|equal opportunity|apply for this job)\b", re.I)
_DUTY = re.compile(r"^(?:you (?:will|will be)|you'll|build\w*|design\w*|develop\w*|implement\w*|"
                   r"integrat\w*|train\w*|evaluat\w*|deploy\w*|test\w*|validat\w*|automat\w*|"
                   r"monitor\w*|maintain\w*|operate\w*|debug\w*|use|write)\b", re.I)
_SKILL_CUE = re.compile(r"\b(?:experience|proficien\w*|knowledge|familiarity|hands.on|required|must have|skills?)\b", re.I)
_SENTENCE = re.compile(r"\b(?:we|you|they|who|which|because|will|should|would|"
                       r"working|building|shipping|delivering|supporting)\b", re.I)
_CREDENTIAL = re.compile(r"\b(?:degree|certifications?|certified|exam|bachelor|master'?s|ph\.?d)\b", re.I)
_TITLE = re.compile(r"\b(?:engineer|developer|manager|team lead|analyst|consultant|architect)\b", re.I)
_FILLER = re.compile(r"\b(?:experience|knowledge|understanding|ecosystem|proficiency|familiarity)\b", re.I)
_NON_SKILL = {"make", "saas", "systems", "software", "testing", "engineering", "cloud", "platform", "tools",
              "customer success", "implementation", "sales", "enterprise technology"}
_OPTIONAL = re.compile(r"(?:\bis (?:a plus|preferred|optional|an advantage)\b|\(preferred\)|"
                       r"\b(?:ideally|nice to have|bonus|would be helpful)\b)", re.I)
_EXAMPLES = re.compile(r"\b(?:such as|including|tools like|one (?:or more )?of|e\.g\.)\s*[:,]?\s*", re.I)
_PROVIDER_TOOLS = frozenset(vocabulary.TOOLS)
_PRACTICE_WORDING = {
    "model training": re.compile(r"\btrain(?:ed|ing|s)?\b.{0,40}\b(?:ml|machine learning) models?\b", re.I),
    "data pipelines": re.compile(r"\bpipelines?\b.{0,45}\bdatasets?\b", re.I),
    "alerting": re.compile(r"\b(?:configur\w*|defin\w*|writ\w*|wrote|tun\w*|set up|maintain\w*|build\w*|built|"
                           r"validat\w*|test\w*|implement\w*|creat\w*)\b[^.;]{0,100}"
                           r"\b(?:alerts?|alert rules?|alertmanager routing|pagerduty notifications?)\b", re.I),
}
_RELATED_TOOLS = {"datadog": ("prometheus", "grafana", "alertmanager", "opensearch", "sentry", "new relic", "dynatrace")}
_NEGATED = re.compile(r"\b(?:did not|didn['’]t|never|without|no hands-on experience)\s+"
                      r"(?:use|implement|build|test|work with|experience with)?", re.I)


@dataclass(frozen=True)
class Skill:
    label: str
    options: tuple[str, ...]
    required: bool
    source: str


def key(term: str) -> str:
    norm = evidence.normalize(vocabulary.canonical(term))
    return _SAME_SKILL.get(norm, norm)


def _short_skill(term: str) -> bool:
    words = term.split()
    if not words or key(term) in _NON_SKILL or not vocabulary.is_technical(term):
        return False
    if vocabulary.canonical(term) in vocabulary.ALL:
        return True
    return len(words) <= 4 and not (_SENTENCE.search(term) or _TITLE.search(term) or _CREDENTIAL.search(term)
                                     or _FILLER.search(term)) and (
        any(t for _, _, t in vocabulary.find_terms(term) if not vocabulary.is_people_skill(t))
        or any(re.search(r"[a-z][A-Z]|\d", word) for word in words)
        or all(word[:1].isupper() for word in words))


def options(label: str) -> tuple[str, ...]:
    if vocabulary.canonical(label) in vocabulary.ALL:
        parts = [label]
    elif re.search(r"\s+or\s+", label, re.I):
        parts = re.split(r",?\s+or\s+", label, flags=re.I)
    elif "/" in label:
        spans = vocabulary.find_terms(label)
        parts = [t for _, _, t in spans] if len(spans) >= 2 else re.split(r"\s*/\s*", label)
    elif m := re.fullmatch(r"(.+?)\s*\((.+)\)", label):
        parts = [m[2]] if _short_skill(m[2]) else [m[1]]
    else:
        parts = [label]
    return tuple(dict.fromkeys(key(part) for word in parts for part in ats.skill_terms(word) if _short_skill(part)))


@lru_cache(maxsize=512)
def _forms(term: str) -> tuple[str, ...]:
    return (term, *(a for a, target in evidence.ALIASES.items()
                     if target == term and a not in _AMBIGUOUS_ALIASES),
            *(a for a, target in _SAME_SKILL.items() if target == term),
            *(a for a, target in vocabulary.ALIASES.items()
              if key(target) == term and a not in _AMBIGUOUS_ALIASES))


@lru_cache(maxsize=4096)
def _named(text: str) -> frozenset[str]:
    names = {key(name) for start, end, name in vocabulary.find_terms(text)
             if text[start:end].casefold() not in _AMBIGUOUS_ALIASES}
    return frozenset(names | {label for label, pattern in _PRACTICE_WORDING.items() if pattern.search(text)})


def mentions(text: str, terms: tuple[str, ...]) -> bool:
    names = _named(text)
    for term in terms:
        if term in names:
            return True
        if vocabulary.canonical(term) in vocabulary.CASE_SENSITIVE:
            if term == "c" and text.strip() == "C" or term == "go" and re.search(r"\b(?:in|using|with) Go\b", text):
                return True
        elif any(ats.keyword_in(text, alias) for alias in _forms(term)):
            return True
    return False


def supports(text: str, terms: tuple[str, ...]) -> bool:
    """A skill named only in a disclaimer is not proof."""
    for term in terms:
        line = re.sub(r"\bRAG[- ]style\b", "", text, flags=re.I) if term == "rag" else text
        if mentions(line, (term,)) and not any(
                mentions(re.split(r"[.;]", line[m.end():], maxsplit=1)[0][:80], (term,)) for m in _NEGATED.finditer(line)):
            return True
    return False


def relates(text: str, terms: tuple[str, ...]) -> bool:
    """Show transferable experience without crediting a different product as direct evidence."""
    return any((alternatives := _RELATED_TOOLS.get(term)) and supports(text, alternatives) for term in terms)


def profile_evidence(profile, terms: tuple[str, ...]) -> str | None:
    from app.bank import verified

    lines = (profile.skills + [s for group in profile.skill_groups for s in group.skills]
             + [r.text for r in verified(profile)]
             + [line for owner in [*profile.experience, *profile.projects]
                for line in [*owner.skills, *owner.highlights]]
             + [p.description for p in profile.projects])
    return next((line for line in lines if supports(line, terms)), None)


def _posting_lines(description: str) -> list[tuple[str, bool]]:
    accepted = []
    for kind, heading, lines in posting.sections(description):
        if kind == "ignore" or _BOILERPLATE.search(heading):
            continue
        for line in lines:
            if _BOILERPLATE.search(line):
                break
            if _CREDENTIAL.search(line) or posting.LOGISTICS.search(line):
                continue
            if (kind in {"required", "preferred"} or kind == "duties" and _DUTY.search(line)
                    or kind == "intro" and (_SKILL_CUE.match(line) or _short_skill(line))):
                for clause in re.split(r";\s*(?=[A-Za-z])", line):
                    accepted.append((clause, kind != "preferred" and not _OPTIONAL.search(clause)))
    return accepted


def _line_labels(line: str) -> list[str]:
    spans = [(start, end, term) for start, end, term in vocabulary.find_terms(line)
             if _short_skill(term) and line[start:end].casefold() not in _AMBIGUOUS_ALIASES]
    examples = []
    for marker in _EXAMPLES.finditer(line):
        tail = line[marker.end():]
        punctuation = re.search(r"[.;](?=\s|$)", tail)
        stop = marker.end() + (punctuation.start() if punctuation else len(tail))
        tools = [span for span in spans if marker.end() <= span[0] < stop and span[2] in _PROVIDER_TOOLS]
        labels = list(dict.fromkeys(tool[2] for tool in tools))
        if len(labels) > 1:
            examples.append(" or ".join(labels))
            spans = [span for span in spans if span not in tools]
    return posting._with_alternatives(line, spans) + examples


def for_job(job) -> list[Skill]:
    """Keep short, concrete skills and their exact source. Explicit saved skills can fill extraction gaps."""
    description = job.description or ""
    manual = job.technical_skills_override
    lines = _posting_lines(description) if not manual else []
    results: dict[tuple[str, ...], Skill] = {}
    extracted = []
    for line, required in lines:
        extracted.extend((label, required, line) for label in _line_labels(line))
        extracted.extend((label, required, line) for label, pattern in _PRACTICE_WORDING.items() if pattern.search(line))
    discovered = [(label, options(label), required, line) for label, required, line in extracted]

    def add(label: str, required: bool, source: str):
        terms = options(label)
        identity = tuple(sorted(terms))
        previous = results.get(identity)
        if terms and (previous is None or required and not previous.required):
            results[identity] = Skill(label, terms, required, source)

    for required, listed in ((True, job.required_skills), (False, job.preferred_skills)):
        for label in listed:
            if label.casefold() == job.title.casefold():
                continue
            terms = options(label)
            source = next((line for line, _ in lines if mentions(line, terms)), "")
            # Use an unscoped saved list only when the posting provides no identifiable technical requirements.
            if terms and (source or not discovered):
                if source:
                    exact = [req for _, found, req, _ in discovered if set(found) == set(terms)]
                    overlaps = any(set(found) & set(terms) and len(found) > 1 for _, found, _, _ in discovered)
                    narrower = any(line == source and mentions(name, terms) for name, _, _, line in discovered)
                    if not exact and (overlaps or narrower or len(terms) > 1):
                        continue
                    required_in_posting = any(req for line, req in lines if mentions(line, terms))
                    add(label, any(exact) if exact else required and required_in_posting, source)
                else:
                    add(label, required, label)
    for label, required, line in extracted:
        add(label, required, line)
    return sorted(results.values(), key=lambda requirement: not requirement.required)


def split(job) -> tuple[list[str], list[str]]:
    items = for_job(job)
    return ([r.label for r in items if r.required], [r.label for r in items if not r.required])
