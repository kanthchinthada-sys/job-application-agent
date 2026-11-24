"""Core Skills for one job: 8-10 compact groups a recruiter scans in seconds, most relevant first.

1. Candidates: every skill you have (your skill lists, per role and project, and tools named in your VERIFIED
   experience records). Nothing is added because the posting names it.
2. Each skill is weighed by the posting: a skill a required qualification or a listed duty asks for counts most,
   then a preferred one; a skill your records show in actual work counts more than one that is only listed.
3. Skills go under plain category labels ("API testing", "CI/CD", "Data validation"); each skill once. The
   categories with the most weight for this job are kept (8-10), strongest first.
4. Ways of working ("align stakeholders", "independently successful") are not skills and are never listed. A
   leadership skill ("test strategy", "mentoring") is listed only when a verified record shows that work.

The private review gets, per skill, why it is there (the posting line) and the records that show it.
"""

import re

from app import evidence as ev
from app.bank import verified

MIN_LINES, MAX_LINES = 8, 10        # compact groups, ordered by relevance to the posting
MAX_LINE_CHARS = 105            # label + skills on one printed line at full width
MAX_PER_LINE = 7

# (label, pattern on the skill name). The first category whose pattern matches is the skill's home.
CATEGORIES: list[tuple[str, str]] = [
    ("Test leadership", r"test (planning|plans?|strategy|strategies)|defect triage|mentor|release (validation|"
                        r"coordination|readiness)|risk-based|technical leadership|uat coordination|test lead"),
    ("API testing", r"rest assured|postman|pact\b|(?<!fast)\bapis?\b|contract testing|microservices testing|soapui|"
                    r"karate"),
    ("Data validation", r"\bsql\b|etl|source-to-target|reconcil|great expectations|data factory|databricks|"
                        r"pyspark|pandas|oracle|mysql|postgres|sqlite|liquibase|data (validation|quality)|"
                        r"snowflake|informatica"),
    ("Kubernetes and deployment", r"kubernetes|helm|\bkind\b|rancher|rke2|docker|linkerd|traefik|nginx|"
                                  r"client-go|deployment|upgrade validation|containers?"),
    ("Troubleshooting and observability", r"opensearch|prometheus|grafana|application logs|debug|"
                                          r"troubleshoot|root cause|log analysis|observab|monitoring|splunk|"
                                          r"elk|datadog"),
    ("Performance testing", r"\bk6\b|jmeter|artillery|gatling|load testing|performance testing|locust"),
    ("CI/CD", r"jenkins|github actions|gitlab|azure devops|gradle|maven|\bgit\b|bitbucket|github(?! copilot)|\bsvn\b|"
              r"ci/cd|pipelines?|teamcity|circleci"),
    ("Test automation", r"selenium|playwright|cucumber|testng|junit|pytest|appium|\buft\b|cypress|vitest|"
                        r"testcontainers|awaitility|ginkgo|gomega|page object|bdd|test framework|"
                        r"regression testing|end-to-end testing|asynchronous testing|mockito|webdriver"),
    ("AI testing", r"\bllm|ollama|copilot|claude|ai-generated|test scenario generation|prompt|deepeval"),
    ("Languages", r"^(java|python|typescript|javascript|go|groovy|kotlin|scala|c#|ruby|bash)$"),
    ("Web frameworks", r"fastapi|angular|react|spring boot|spring cloud|node|rails|django|flask"),
    ("Cloud and messaging", r"\baws\b|azure|gcp|linux|rabbitmq|kafka|redis|\bnfs\b|sns|sqs|cloud"),
    ("Healthcare standards", r"\bfhir\b|\bhl7\b|dicom"),
    ("Test management", r"testrail|jira|zephyr|sonarqube|confluence|xray|code review"),
]
_LEADERSHIP = "Test leadership"
# what a posting says when it asks for that kind of work (its duties and qualifications, not only tool names)
_ASKS = {
    "Test leadership": r"\blead|mentor|coach|test strategy|testing strategy|roadmap|release readiness|test planning",
    "API testing": r"\bapis?\b|rest|web services|contract testing|backend",
    "Data validation": r"\bsql\b|etl|elt|data (validation|pipelines?|quality)|databases?",
    "Kubernetes and deployment": r"kubernetes|deploy|helm|containers?|docker|test environments?",
    "Troubleshooting and observability": r"troubleshoot|debug|diagnos|observab|monitor|logs|incident|root cause",
    "Performance testing": r"performance|load test|non-functional",
    "CI/CD": r"ci/cd|\bci\b|pipelines?|jenkins|continuous (integration|deployment|delivery)",
    "Test automation": r"test automation|automated test|testing frameworks?|automation frameworks?|"
                       r"ui (test|automation)|end-to-end (test|automation)|regression|automate at every layer|"
                       r"\b(playwright|selenium|cypress)\b",
    "AI testing": r"\bai\b|llm|agentic|ai-enabled|machine learning",
    "Languages": r"programming|languages?|\b(java|python|typescript|javascript|ruby)\b",
    "Web frameworks": r"web applications?|react|rails|frontend|front-end|backend|full[- ]stack",
    "Cloud and messaging": r"\b(aws|gcp|azure|cloud|kafka|sqs|sns|messaging)\b",
    "Healthcare standards": r"fhir|hl7|healthcare",
    "Test management": r"test management|zephyr|testrail|sonarqube|code quality",
}
# the work a leadership skill names, as a record would say it
_LEAD_EVIDENCE = {
    "test planning": r"test plan|planning", "test plans": r"test plan", "test strategy": r"strateg",
    "defect triage": r"triage", "mentoring": r"mentor", "release validation": r"release",
    "release coordination": r"release", "release readiness": r"release", "technical leadership": r"\bled\b|\blead",
    "risk-based testing": r"risk-based",
}


# a dropped category whose named tools belong in a kept line without changing what that line says
_CLOSEST = {
    "API testing": ("Test automation",),          # the line is then "Test automation and API testing"
    "Data validation": ("Languages",),            # SQL only: it is a language
    "Kubernetes and deployment": ("CI/CD",),      # the line is then "CI/CD and containers"
    "Cloud and messaging": ("CI/CD",),            # AWS / Azure: "CI/CD, containers and cloud"
}
_FOLDS = {"API testing": lambda s: _named_tool(s), "Data validation": lambda s: s.lower() == "sql",
          "Kubernetes and deployment": lambda s: s.lower() in ("docker", "kubernetes", "helm"),
          "Cloud and messaging": lambda s: s.lower() in ("aws", "azure", "gcp")}
# kinds of testing a posting names, and the tools of yours that do that kind of testing
_DOES = [
    (r"\bweb\b|\bui\b|browser|end-to-end test|front-?end", ("playwright", "selenium", "cypress", "webdriver")),
    (r"\bapis?\b|backend|web services", ("rest assured", "postman", "pact")),
    (r"databases?|data pipelines?|\betl\b|\belt\b", ("sql",)),
]


_CASING = {"gcp": "GCP", "aws": "AWS", "artillery": "Artillery", "kafka": "Kafka", "sqs": "SQS", "sns": "SNS",
           "amazon sns": "Amazon SNS", "sonarqube": "SonarQube", "zephyr": "Zephyr", "jmeter": "JMeter",
           "gatling": "Gatling", "locust": "Locust", "splunk": "Splunk", "circleci": "CircleCI", "grpc": "gRPC"}


def display(skill: str) -> str:
    """A skill as a resume prints it: "GCP", "Artillery", "SonarQube" (your list may have it in lowercase)."""
    return _CASING.get(skill.lower(), skill) if skill.islower() else skill


def _named_tool(skill: str) -> bool:
    from app.selection import KNOWN_TECH
    return skill.lower() in {t.lower() for t in KNOWN_TECH}


def category_of(skill: str) -> str | None:
    low = skill.lower()
    return next((label for label, rx in CATEGORIES if re.search(rx, low)), None)


def is_listable(skill: str) -> bool:
    """A skill a recruiter would look for: a tool, language, platform or engineering practice. Ways of working
    ("align stakeholders", "focus on quality and maintainability") are shown by your bullets, never listed."""
    from app.selection import SOFT, SOFT_PART
    from app.vocabulary import is_people_skill
    if len(skill) > 40 or category_of(skill) is None:
        return False
    if not _named_tool(skill) and (len(skill.split()) > 3 or re.search(
            r"\b(methodolog\w*|solutions?|capabilit\w*|experience|skills?|knowledge|understanding|expertise|"
            r"implementation|practices|principles|concepts|environment)\b", skill, re.IGNORECASE)):
        return False                     # a posting's phrase ("data validation methodologies"), not a skill name
    if skill.lower() in _LEAD_EVIDENCE:
        return True                      # test leadership work (still needs a record of it: see core_skills)
    if is_people_skill(skill) or SOFT_PART.search(skill):
        return False
    if not _named_tool(skill) and re.match(r"(debug|build|write|align|deliver|drive|focus|ensure|use|work|"
                                           r"collaborate|communicate|own)\b", skill, re.IGNORECASE):
        return False                     # a verb phrase ("Debug failures"): a duty, not a skill name
    return not (SOFT.search(skill) and category_of(skill) == _LEADERSHIP and not re.search(r"mentor|triage", skill, re.I))


def core_skills(profile, job, plan, candidates: list[str], debug: dict | None = None
                ) -> tuple[list[str], list[dict], list[dict]]:
    """(skills in order, 3-5 lines [{name, skills}], review entries). ``candidates``: your skills, as
    selection.select_skills lists them (already only skills you have)."""
    from app.selection import _NOT_SKILL, SOFT
    records = verified(profile)
    chosen_ids = {i for ids in plan.selected.values() for i in ids}
    vocab = ev.vocabulary(profile)
    kind_weight = {"required": 6.0, "responsibility": 5.0, "preferred": 3.0}
    reqs = [q for q in plan.requirements if q.kind in kind_weight]

    def shown_in(skill: str) -> list[str]:
        low = skill.lower()
        return [r.id for r in records if ev._has(r.text.lower(), low)]

    scored: dict[str, dict] = {}
    for skill in dict.fromkeys(candidates):
        if not is_listable(skill) or skill.lower() in {s.lower() for s in scored}:
            continue
        home = category_of(skill)
        ids = shown_in(skill)
        if home == _LEADERSHIP:
            pattern = _LEAD_EVIDENCE.get(skill.lower(), re.escape(skill.lower()))
            ids = [r.id for r in records if re.search(pattern, r.text, re.IGNORECASE)]
            if not ids:
                continue                  # a leadership claim needs a record of that work
        weight, why = 0.0, None
        for q in reqs:
            m = ev.match_requirement(profile, q.text, [("s", skill)], vocab)
            # a part about people or customers ("customer-facing") never makes a tool asked for ("client-go");
            # a related match (REST Assured for "API testing") counts a little less than the posting naming it
            hits = [e for e in m.evidence if not (_NOT_SKILL.search(e.part) or SOFT.search(e.part))]
            # (only a line about testing: "building web applications" doesn't ask for Playwright)
            does = bool(re.search(r"\btest|automat|validat|\bqa\b", q.text, re.IGNORECASE)) and any(
                re.search(rx, q.text, re.IGNORECASE) and any(t in skill.lower() for t in tools) for rx, tools in _DOES)
            if not hits and not does:
                continue
            value = kind_weight[q.kind] * (1.0 if any(not e.related for e in hits) else 0.85)
            if value > weight:
                weight, why = value, f"{q.kind}: {q.text[:90]}"
        if ids:
            weight += 1.5 if set(ids) & chosen_ids else 0.8     # shown in work (on this resume: more)
            weight += min(1.0, 0.15 * len(ids))                # central to your work: in many records
        else:
            weight += 0.2                                      # listed in your skills only
        if home not in ("Languages",) and not _named_tool(skill):
            weight -= 0.5                                      # recruiters scan for tools; practices after them
        scored[skill] = {"skill": skill, "category": home, "weight": weight, "why": why, "records": ids[:3]}

    if debug is not None:
        debug.update(scored)
    by_cat: dict[str, list[dict]] = {}
    for s in sorted(scored.values(), key=lambda s: -s["weight"]):
        by_cat.setdefault(s["category"], []).append(s)
    # how much the posting's duties and qualifications ask for each kind of work (required/duties count most)
    # (a posting lists what matters most first: within each kind, earlier lines count more)
    position: dict[int, float] = {}
    seen_kind: dict[str, int] = {}
    for n, q in enumerate(reqs):
        k = seen_kind.get(q.kind, 0)
        seen_kind[q.kind] = k + 1
        position[n] = max(0.5, 1.0 - 0.06 * k)
    ask = {label: sum(kind_weight[q.kind] * position[n] for n, q in enumerate(reqs)
                      if re.search(rx, q.text, re.IGNORECASE)) for label, rx in _ASKS.items()}

    # a category's weight: what the employer hires for first (how much the posting's duties and qualifications ask
    # for that kind of work), then how strong your skills in it are (its 4 strongest, so one long list of minor
    # tools doesn't win)
    # the kind of role decides which line leads when the posting asks for several things about equally: an SDET
    # role leads with test automation, a deployment FDE with Kubernetes, a data lead with data validation
    lead_line = {"sdet": "Test automation", "fde_infra": "Kubernetes and deployment", "lead": _LEADERSHIP,
                 "data_lead": "Data validation", "fde_app": "Web frameworks"}.get(getattr(plan, "family", ""))

    def cat_weight(label: str, items: list[dict]) -> float:
        return (0.5 * sum(s["weight"] for s in items[:4]) + 0.6 * min(40.0, ask.get(label, 0.0))
                + (6.0 if label == lead_line and ask.get(label, 0.0) >= 3 else 0.0))
    ranked = sorted(by_cat.items(), key=lambda kv: -cat_weight(*kv))
    asked = [kv for kv in ranked if any(s["why"] for s in kv[1]) or ask.get(kv[0], 0) >= 3]
    # more kinds of work asked for than lines: related lines share one ("CI/CD and containers") before any
    # asked-for line is dropped
    for source in ("Cloud and messaging", "Kubernetes and deployment", "API testing", "Data validation"):
        names = [label for label, _ in asked]
        if len(asked) > MAX_LINES and source in names and _CLOSEST[source][0] in names:
            asked = [kv for kv in asked if kv[0] != source]
    keep = asked[:MAX_LINES]
    # the languages a posting requires are always shown (a short line); the weakest other line makes room
    langs = next((kv for kv in asked if kv[0] == "Languages" and any(s["why"] for s in kv[1])), None)
    if langs and langs not in keep:
        keep = keep[:MAX_LINES - 1] + [langs]
    for kv in ranked:                     # at least 3 lines: then the categories your work shows most
        if len(keep) >= MIN_LINES:
            break
        if kv not in keep:
            keep.append(kv)
    keep.sort(key=lambda kv: -cat_weight(*kv))
    if langs in keep:                     # languages last: the work-type lines are what a recruiter scans first
        keep.remove(langs)
        keep.append(langs)

    # a category that didn't make the 5 lines: the skills the posting asks for still show, in the closest line
    # kept ("REST Assured" with test automation, "SQL" with languages), when it has room
    folded: dict[str, list[dict]] = {}
    kept = [label for label, _ in keep]
    for label, items in ranked:
        if label in kept:
            continue
        home = next((h for h in _CLOSEST.get(label, ()) if h in kept), None)
        if home:
            folded.setdefault(home, []).extend(x for x in items[:4] if x["why"] and x["weight"] >= 4.5
                                               and x["records"] and _FOLDS[label](x["skill"]))

    groups, review = [], []
    # the lines chosen, then (only while fewer than 3 lines have 2+ skills) the next categories your work shows
    order = keep + [kv for kv in ranked if kv not in keep]
    for n, (label, items) in enumerate(order):
        if n >= len(keep) and len(groups) >= MIN_LINES:
            break
        line, used = [], len(label) + 2
        # named tools first, then at most one practice ("API testing", "ETL validation") at the end
        # (test leadership is all practices: up to four of them)
        tools = [x for x in items if _named_tool(x["skill"]) or label in ("Languages", _LEADERSHIP)]
        extra = folded.get(label, [])
        tools = tools[:max(3, MAX_PER_LINE - len(extra) - 1)] + extra + tools[max(3, MAX_PER_LINE - len(extra) - 1):]
        practices = [x for x in items if x not in tools][:1]
        for s in tools + practices:
            if len(line) >= MAX_PER_LINE or used + len(s["skill"]) + 2 > MAX_LINE_CHARS:
                continue
            low = s["skill"].lower()
            if any(low in x.lower() or x.lower() in low for x in line):
                continue                  # "Selenium" and "Selenium WebDriver": once
            if len(line) >= 6 and not s["why"]:
                continue                  # a long line only for what the posting asks
            if not s["why"] and s["weight"] < 1.0 and len(line) >= 3:
                continue                  # only listed, nothing asks for it: room is better left
            line.append(display(s["skill"]))
            used += len(s["skill"]) + 2
            review.append({"line": label, "skill": display(s["skill"]), "why": s["why"] or "shown in your work",
                           "records": s["records"]})
        if len(line) >= 2 or (line and label == "Languages"):
            homes = {category_of(x) for x in line}
            name = label
            if label == "Test automation" and "API testing" in homes:
                name = "Test automation and API testing"
            elif label == "CI/CD" and homes & {"Kubernetes and deployment", "Cloud and messaging"}:
                parts = (["containers"] if "Kubernetes and deployment" in homes else []) + (
                    ["cloud"] if "Cloud and messaging" in homes else [])
                name = "CI/CD and " + " and ".join(parts) if len(parts) == 1 else "CI/CD, containers and cloud"
            groups.append({"name": name, "skills": line})
        else:
            review = [r for r in review if r["line"] != label]
    return [s for g in groups for s in g["skills"]], groups, review
