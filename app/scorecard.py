"""How a recruiter and hiring manager would read your resume for one job: role family, bullet strength,
role-specific priorities, a 3-5 criterion hiring scorecard, and a private review.

Used by app/selection.py (to choose and order bullets) and app/tailoring.py (to report). Nothing here writes
resume text: it only scores and explains your own verified bullets.

Rules follow common recruiter practice (structured-hiring scorecards, the XYZ bullet formula, 2025-26 FDE /
SDET / QA-lead postings):
* direct evidence of a required qualification first; then preferred; transferable evidence below direct
* among equals: a measured result > a stated scope > a concrete outcome > a duty
* ownership verbs as written in your bullet ("built", "led" > "developed" > "contributed", "supported")
* a tool named only in Skills is not evidence that you used it for anything
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# --- role family (from the posting text, not just its title) ------------------------------------------------

_FAMILY_SIGNALS: dict[str, tuple[str, ...]] = {
    "fde": ("forward deployed", "forward-deployed", "fde", "customer environment", "customer-facing", "onsite",
            "deployment engineer", "implementation engineer", "solutions engineer", "field engineer"),
    "infra": ("kubernetes", "k8s", "helm", "terraform", "on-prem", "air-gapped", "vpc", "observability",
              "prometheus", "grafana", "opensearch", "linux", "networking", "rancher", "rke2", "openshift",
              "deployment", "troubleshoot", "incident"),
    "sdet": ("sdet", "test automation", "automation framework", "automated test", "test framework", "playwright",
             "selenium", "cypress", "rest assured", "restassured", "appium", "regression", "quality engineer",
             "engineer in test", "automation engineer"),
    "lead": ("qa lead", "test lead", "qe lead", "quality lead", "lead ", "test strategy", "release readiness",
             "go/no-go", "mentor", "coordinate", "test manager", "qa manager", "people management"),
    "data": ("etl", "data validation", "data warehouse", "reconciliation", "databricks", "pyspark", "spark",
             "data pipeline", "sql", "data quality", "informatica", "snowflake"),
    "ai": ("llm", "agent", "agentic", "genai", "generative", "machine learning", "rag", "prompt", "evaluation"),
}


def _count(text: str, words) -> int:
    low = f" {text.lower()} "
    return sum(len(re.findall(rf"(?<![\w]){re.escape(w.strip())}(?![\w])", low)) for w in words)


class Family(BaseModel):
    name: str                 # fde_infra | fde_app | sdet | lead | data_lead | general
    label: str                # for people
    data_heavy: bool = False
    ui_heavy: bool = False
    customer_facing: bool = False
    tools_named: list[str] = []


def family(title: str, description: str) -> Family:
    t, d = title.lower(), f"{title} {description}".lower()
    score = {k: _count(d, v) for k, v in _FAMILY_SIGNALS.items()}
    data_heavy = score["data"] >= 4
    ui_heavy = bool(re.search(r"\b(ui|web|browser|front[- ]?end|playwright|cypress|selenium)\b", d))
    customer = bool(re.search(r"customer|client|stakeholder|onsite|field", d))
    if re.search(r"forward[- ]deployed|\bfde\b|deployment engineer|implementation engineer|field engineer", t) \
            or (score["fde"] >= 3 and "test" not in t):
        name = "fde_infra" if score["infra"] >= 4 else "fde_app"
        label = ("Forward Deployed Engineer (deployment / infrastructure)" if name == "fde_infra"
                 else "Forward Deployed Engineer (building product for customers)")
    elif re.search(r"\b(ai|ml|llm|genai|machine learning)\b", t) and re.search(r"qa|quality|test|evaluat|evals?\b", t):
        name, label = "ai_qa", "AI QA / LLM evaluation"
    elif re.search(r"\b(manager|head of|director)\b", t) and re.search(r"qa|quality|test|qe\b", t):
        name, label = "manager", "QA Manager"
    elif re.search(r"\b(lead|head)\b", t) and re.search(r"qa|quality|test|qe\b", t):
        name = "data_lead" if data_heavy else "lead"
        label = "QA / Test Lead (data validation)" if data_heavy else "QA / Test Lead"
    elif re.search(r"sdet|in test|quality engineer|software engineer", t) and "automation engineer" not in t:
        name, label = "sdet", "SDET / Senior Quality Engineer"
    elif re.search(r"automation|qa engineer|test engineer|tester", t) or score["sdet"] >= 4:
        name, label = "qa_automation", "QA Automation Engineer"
    else:
        name, label = "general", "General engineering role"
    return Family(name=name, label=label, data_heavy=data_heavy, ui_heavy=ui_heavy, customer_facing=customer)


# --- bullet strength (job-independent) --------------------------------------------------------------------

OWNER_VERBS = ("built", "designed", "architected", "created", "established", "defined", "led", "owned", "drove",
               "launched", "introduced", "initiated", "spearheaded")
EXEC_VERBS = ("developed", "implemented", "automated", "integrated", "deployed", "migrated", "reduced", "improved",
              "debugged", "resolved", "investigated", "diagnosed", "extended", "added", "refactored", "validated",
              "verified", "configured", "optimized", "tested", "executed", "wrote", "maintained", "standardized",
              "consolidated", "replaced", "converted", "analyzed", "measured", "mentored", "coordinated", "planned",
              "prioritized", "managed", "earned")
WEAK_VERBS = ("contributed", "supported", "assisted", "participated", "helped", "worked", "involved", "responsible",
              "collaborated", "documented", "demonstrated", "presented", "communicated", "shared", "attended")

_BASELINE = re.compile(r"\bfrom\s+[\d.,]+\s*\w*\s+to\s+[\d.,]+|\b\d+(\.\d+)?\s*%|\b(\d+(\.\d+)?)x\b|"
                       r"\b(reduced|cut|improved|increased|decreased|shortened|saved)\b[^.]{0,60}\b\d", re.IGNORECASE)
_SCOPE_NUMBER = re.compile(r"\b\d[\d,]*\+?[- ]?(tests?|test cases|suites?|services|microservices|crds?|pipelines?|"
                           r"jobs?|teams?|engineers?|people|releases?|environments?|clusters?|nodes|users|customers|"
                           r"clients|apis?|endpoints?|tables|rows|records|scenarios|checks|repositories|projects|"
                           r"applications|countries|sites)\b|\bmore than \d|\bover \d", re.IGNORECASE)
_OUTCOME = re.compile(r"\b(award|adopted|became the standard|first|blocked|prevented|caught|zero|eliminated|"
                      r"enabled|unblocked|released|shipped|in production|go-live|go/no-go|sign-off)\b", re.IGNORECASE)
_SCOPE_WORDS = re.compile(r"\b(end-to-end|across|org-wide|company-wide|cross-team|multiple|every|all)\b", re.I)


def first_verb(text: str) -> str:
    m = re.match(r"\s*([A-Za-z-]+)", text)
    return m.group(1).lower() if m else ""


def ownership(text: str) -> tuple[float, str]:
    v = first_verb(text)
    if v in OWNER_VERBS:
        return 2.0, "owner"
    if v in EXEC_VERBS:
        return 1.0, "hands-on"
    if v in WEAK_VERBS:
        return 0.0, "supporting"
    return 0.5, "hands-on"


def proof(text: str) -> tuple[float, str]:
    if _BASELINE.search(text):
        return 3.0, "measured result"
    if _SCOPE_NUMBER.search(text):
        return 2.0, "measured scope"
    if _OUTCOME.search(text):
        return 1.5, "concrete outcome"
    if re.match(r"\s*(responsible for|worked on|involved in|helped with|assisted with)\b", text, re.I):
        return -1.0, "duty"
    if _SCOPE_WORDS.search(text):
        return 0.5, "scope"
    return 0.0, "plain"


def strength(text: str) -> float:
    """How convincing a bullet is on its own: proof first, ownership second."""
    return proof(text)[0] + 0.75 * ownership(text)[0]


# --- what each role family looks for first ---------------------------------------------------------------

# (pattern on your bullet, points, what it shows)
ROLE_RULES: dict[str, list[tuple[str, float, str]]] = {
    "fde_infra": [
        (r"kubernetes|helm|rancher|rke2|cluster|pod|namespace|sidecar|linkerd|crd", 2.5, "Kubernetes / Helm in real environments"),
        (r"deploy|upgrade|install|rollout|readiness|release verification|environment", 2.0, "deployment validation"),
        (r"investigat|debug|troubleshoot|diagnos|root cause|isolate|failure", 2.0, "debugging real failures"),
        (r"opensearch|prometheus|grafana|observab|logs?\b|metrics|dashboards|monitor", 1.5, "observability"),
        (r"integrat|api|interface|fhir|hl7|connector", 1.0, "integration work"),
        (r"customer|client", 2.0, "customer-facing work"),
        (r"stakeholder|developers|teams", 0.5, "collaboration (internal)"),
    ],
    "fde_app": [
        (r"built|developed|implemented|code|service|application|frontend|backend|fastapi|typescript|angular", 2.0, "software you built"),
        (r"customer|client", 2.0, "customer-facing work"),
        (r"integrat|api|connector|data flow|pipeline", 1.5, "integrations and data flows"),
        (r"llm|model|agent|ai\b|ollama|prompt", 1.5, "AI applications"),
        (r"deploy|production|release|rollout", 1.0, "shipping to production"),
        (r"investigat|debug|troubleshoot|root cause", 1.0, "debugging"),
        (r"stakeholder|requirements|scop", 0.5, "scoping with stakeholders"),
    ],
    "sdet": [
        (r"framework", 3.0, "automation framework you built"),
        (r"playwright|selenium|cypress|ui\b|browser|web", 1.5, "UI automation"),
        (r"rest assured|api|endpoint|contract", 1.5, "API automation"),
        (r"jenkins|github actions|gitlab|ci/cd|ci\b|pipeline|azure devops", 2.0, "CI/CD integration"),
        (r"regression|risk", 1.0, "regression strategy"),
        (r"defect|prevent|shift-left|root cause", 1.0, "defect prevention"),
        (r"kubernetes|helm|microservices|java|junit|testng|pytest", 1.0, "engineering depth"),
        (r"flak|stability|stable|runtime|faster|feedback", 1.5, "reliable, fast suites"),
    ],
    "lead": [
        (r"strategy|roadmap|test plan|approach|planning", 3.0, "test strategy"),
        (r"release readiness|go/no-go|release risk|sign-off|release decision", 2.5, "release readiness"),
        (r"mentor|led\b|lead\b|coached|onboard|trained", 2.0, "leading and mentoring"),
        (r"report|metrics|status|escape|trend|dashboard", 2.0, "quality metrics and reporting"),
        (r"coordinat|prioriti|stakeholder|triage", 1.5, "coordinating execution"),
        (r"automation|framework|jenkins", 1.0, "hands-on automation"),
    ],
}
# QA Automation: reusable UI and API automation, framework components, CI execution, diagnosing test failures
ROLE_RULES["qa_automation"] = [
    (r"framework|reusable|page object|components?|utilities", 2.5, "reusable automation and framework components"),
    (r"playwright|selenium|cypress|ui\b|browser|web", 2.0, "UI automation"),
    (r"rest assured|postman|api|endpoint", 2.0, "API automation"),
    (r"jenkins|github actions|gitlab|ci\b|pipeline|azure devops|azure pipelines", 2.0, "CI execution"),
    (r"investigat|diagnos|isolat|root cause|failures?|flak", 2.0, "diagnosing test failures"),
]
# Senior SDET: framework ownership, service-level, distributed/async, performance and reliability
ROLE_RULES["sdet"] = ROLE_RULES["sdet"] + [
    (r"microservices?|service|distributed|asynchronous|async|event|queue|readiness", 1.5, "service-level and async testing"),
    (r"\bk6\b|load|latency|throughput|performance|p95|p99", 1.5, "performance testing"),
]
# QA Manager: coaching, work allocation, quality metrics, priority and release-risk decisions
ROLE_RULES["manager"] = [
    (r"mentor|coach|feedback|expectations|onboard|trained", 3.0, "coaching the team"),
    (r"prioriti|assign|allocat|scope|planning|coordinat", 2.5, "allocating and planning work"),
    (r"metrics|report|escape|trend|dashboard|stability|coverage gaps", 2.5, "quality metrics"),
    (r"release readiness|release risk|go/no-go|sign-off|release decision|roadmap", 2.5, "priority and release decisions"),
    (r"strategy|risk-based|test plan", 1.5, "test strategy"),
    (r"triage|defect", 1.0, "defect triage"),
]
# AI QA / LLM evaluation: implemented evaluations, regression tests, unsupported-claim checks, failures investigated
ROLE_RULES["ai_qa"] = [
    (r"evaluat|deepeval|ragas|metamorphic|faithfulness|relevance", 3.0, "implemented evaluations"),
    (r"regression|gates?|baseline|consisten", 2.0, "regression tests for model changes"),
    (r"unsupported|claims?|hallucinat|grounded|evidence references", 2.5, "checks for unsupported claims"),
    (r"llm|language model|model|prompt|ollama|agent", 1.5, "LLM applications"),
    (r"investigat|malformed|failure|schema validation|fake model", 2.0, "failures investigated"),
]
ROLE_RULES["data_lead"] = ROLE_RULES["lead"] + [
    (r"etl|sql|reconcil|source-to-target|data quality|databricks|pyspark|data factory|transformation", 2.5,
     "ETL / data validation"),
]


def role_points(fam: Family, text: str, posting_low: str) -> tuple[float, list[str]]:
    """Points for what this kind of role looks for first, and the labels that earned them."""
    low = text.lower()
    rules = list(ROLE_RULES.get(fam.name, []))
    if fam.name == "sdet" and fam.data_heavy:
        rules.append((r"etl|sql|reconcil|data validation|databricks|pyspark", 1.5, "data validation"))
    pts, why = 0.0, []
    for pattern, p, label in rules:
        if re.search(rf"\b(?:{pattern})", low):
            pts += p
            why.append(label)
    # tools the posting names that this bullet uses: what the hiring manager will look for by name
    named = [t for t in _TOOLS if t in low and re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", posting_low)]
    if named:
        pts += min(3.0, 1.5 * len(named))
        why.append("uses " + ", ".join(named[:3]))
    # bullets that only describe talking about work (not doing it) read as filler, except for lead roles
    if first_verb(text) in ("documented", "demonstrated", "presented", "communicated", "shared", "worked",
                            "participated", "attended") and fam.name not in ("lead", "data_lead", "manager"):
        pts -= 4.0
    return pts, why


_TOOLS = ["playwright", "selenium", "cypress", "rest assured", "restassured", "appium", "cucumber", "junit",
          "testng", "pytest", "postman", "k6", "jmeter", "jenkins", "github actions", "gitlab", "azure devops",
          "kubernetes", "helm", "rancher", "rke2", "docker", "terraform", "aws", "azure", "gcp", "linux",
          "opensearch", "prometheus", "grafana", "java", "python", "typescript", "javascript", "go", "sql",
          "databricks", "pyspark", "spark", "oracle", "testrail", "jira", "fastapi", "angular", "react"]


# --- the scorecard and private review -----------------------------------------------------------------------

class Criterion(BaseModel):
    requirement: str                # as the posting says it
    kind: str                       # required | preferred | responsibility
    evidence: str                   # direct | transferable | skills only | missing
    resume_line: str | None = None  # the exact resume bullet that supports it (as printed)
    where: str | None = None        # employer or project, and bullet position
    buried: bool = False            # supported, but not in the top 3 bullets of that role


class Review(BaseModel):
    role_family: str
    what_the_posting_requires: list[str]
    my_interpretation: str          # what the hiring team probably values most (an interpretation, not the posting)
    criteria: list[Criterion]       # the 3-5 that drive the first screen
    top_third: list[str]            # what a recruiter sees in the first 20 seconds
    reasons_to_pass: list[str]      # the strongest reasons a recruiter might pass (at most 3)
    gaps: list[str]                 # required qualifications with no evidence: for you, never in the resume


INTERPRETATION = {
    "fde_infra": "Can you deploy, upgrade and debug their software in real Kubernetes environments, and explain "
                 "what went wrong to the people involved? Customer-facing ownership counts more than internal work.",
    "fde_app": "Can you build and ship production software for a customer's workflow, end to end, and own the "
               "conversation with the customer while doing it? Coding depth is the first filter.",
    "sdet": "Have you built (not only added tests to) an automation framework that others rely on, wired it into "
            "CI/CD, and kept regression suites fast and reliable?",
    "lead": "Have you owned test strategy, led execution across a team, reported quality and release risk, and "
            "made release-readiness calls, while staying hands-on?",
    "data_lead": "Have you led data/ETL testing: strategy, reconciliation and validation in SQL, coordinating "
                 "execution and reporting release readiness?",
    "general": "Do your most recent bullets show you doing this job's core work, with concrete results?",
}


def _weight(text: str, posting_low: str) -> int:
    words = [w for w in re.findall(r"[a-z][a-z0-9+#/.-]{3,}", text.lower())]
    return sum(posting_low.count(w) for w in set(words))


_GENERIC = {"automation", "testing", "test automation", "quality", "software development", "development",
            "engineering", "qa", "software", "experience", "tools", "test scripts", "code"}


def rank_criteria(plan, job) -> list:
    """The plan's requirement rows in the order a first screen weighs them: hard required qualifications the
    posting stresses most first (soft-skill lines and boilerplate after). Shared by the resume's private review
    and the cover letter, so both lead with the same evidence."""
    from app.selection import SOFT
    posting_low = f" {job.title} {job.description} ".lower()
    parts_of = {q.text: q.parts for q in plan.requirements}
    rows = [r for r in plan.rows if r.kind in ("required", "preferred", "responsibility")]
    required = [r for r in rows if r.kind == "required"] or [r for r in rows if r.kind == "responsibility"]

    def hard(r) -> list[str]:
        return [p for p in parts_of.get(r.requirement, []) if not SOFT.search(p) and p not in _GENERIC]

    ranked = sorted(required, key=lambda r: (not hard(r), -sum(posting_low.count(p) for p in set(hard(r))),
                                             -_weight(r.requirement, posting_low)))
    rest = [r for r in rows if r not in ranked and hard(r)]
    return ranked + sorted(rest, key=lambda r: (r.kind != "preferred", -sum(posting_low.count(p) for p in hard(r))))


def printed_records(resume) -> dict[str, tuple[str, str, int]]:
    """Record id -> (text as printed, employer or project, bullet position) for every bullet on the resume."""
    printed: dict[str, tuple[str, str, int]] = {}
    for role in resume.experience:
        for i, b in enumerate(role.bullets):
            if b.source_id:
                printed[b.source_id] = (b.text, role.company, i + 1)
    for pr in resume.projects:            # projects are stored as dicts: highlights + their record ids
        for i, (text, sid) in enumerate(zip(pr.get("highlights", []), pr.get("highlight_ids", []))):
            if sid:
                printed[sid] = (text, f"{pr.get('name')} (project)", i + 1)
    return printed


def requirement_map(plan, resume, profile) -> list[dict]:
    """For you: every requirement of the posting, how your records meet it, and where each record ended up on
    THIS resume (employer and bullet number), or that it was cut to fit the two pages."""
    printed = printed_records(resume)
    owner = {r.id: r.owner for r in profile.experience_bank}
    out = []
    for row in plan.rows:
        records = []
        for rid in row.selected_ids:
            hit = printed.get(rid)
            records.append({"id": rid, "owner": owner.get(rid, ""), "printed": bool(hit),
                            "where": f"{hit[1]}, bullet {hit[2]}" if hit else None,
                            "line": hit[0] if hit else None})
        records.sort(key=lambda r: not r["printed"])
        shown = [r for r in records if r["printed"]]
        out.append({"requirement": row.requirement, "kind": row.kind, "category": row.category,
                    "strength": row.strength, "found": row.found_parts, "missing": row.not_found_parts,
                    "found_elsewhere": row.found_elsewhere,
                    "on_resume": shown[:4], "more_on_resume": max(0, len(shown) - 4),   # the 4 most relevant
                    "not_printed": [r["id"] for r in records if not r["printed"]],
                    "where": "Education" if row.location == ["Education"] else None})
    order = {"required": 0, "responsibility": 1, "preferred": 2}
    cat = {"technical": 0, "education": 1, "soft skill": 2}
    return sorted(out, key=lambda r: (cat.get(r["category"], 3), order.get(r["kind"], 3)))


def build_review(plan, resume, profile, job, fam: Family) -> Review:
    """The private hiring-manager review of a tailored resume (never printed on the resume)."""
    posting_low = f" {job.title} {job.description} ".lower()
    printed = printed_records(resume)

    from app import ats
    from app.selection import SOFT
    parts_of = {q.text: q.parts for q in plan.requirements}
    rows = [r for r in plan.rows if r.kind in ("required", "preferred", "responsibility")]
    generic = _GENERIC
    ordered = rank_criteria(plan, job)
    required_ranked = [r for r in ordered if r.kind == "required"] or ordered
    picked = required_ranked[:5]
    if len(picked) < 3:
        picked += [r for r in ordered if r not in picked][: 3 - len(picked)]
    criteria = []
    for r in picked:
        best = None
        parts = parts_of.get(r.requirement, [])
        # the printed bullet that shows the most of what the line asks for (ties: higher in its role)
        options = [printed[x] for x in r.selected_ids if x in printed]
        if options:
            best = max(options, key=lambda o: (sum(1 if p in generic else 3 for p in parts if ats.resume_has(o[0], p)),
                                               -o[2]))
        evidence = {"gap": "missing"}.get(r.strength, r.strength)
        if r.location == ["Education"]:
            criteria.append(Criterion(requirement=r.requirement, kind=r.kind, evidence=evidence,
                                      resume_line="(Education section)" if evidence == "direct" else None,
                                      where="Education" if evidence == "direct" else None))
            continue
        if evidence in ("direct", "partial", "transferable") and best is None:
            evidence = "skills only" if r.strength != "gap" else "missing"   # selected but cut to fit the page
        criteria.append(Criterion(
            requirement=r.requirement, kind=r.kind, evidence=evidence,
            resume_line=best[0] if best else None,
            where=f"{best[1]}, bullet {best[2]}" if best else None,
            buried=bool(best and best[2] > 3)))

    top = []
    if resume.experience:
        first = resume.experience[0]
        top.append(f"Most recent title: {first.title} at {first.company} (the posting: {job.title}).")
        for b in first.bullets[:3]:
            p = proof(b.text)[1]
            top.append(f"Top bullet: \"{b.text}\" ({p}).")

    reasons = []
    must_missing = [c for c in criteria if c.kind == "required" and c.evidence in ("missing", "skills only")]
    if must_missing:
        reasons.append("Required, with no bullet proving it: " + "; ".join(c.requirement[:80] for c in must_missing[:3]))
    if fam.name.startswith("fde") and fam.customer_facing and not any(
            re.search(r"customer|client", t.lower()) for t, _, _ in printed.values()):
        reasons.append("Customer-facing work: your bullets show internal stakeholders (developers, business teams), "
                       "not external customers. A recruiter will notice; be ready to explain it.")
    transfer = [c for c in criteria if c.kind == "required" and c.evidence == "transferable"]
    if transfer:
        reasons.append("Only related (transferable) evidence for: " + "; ".join(c.requirement[:70] for c in transfer[:2]))
    if resume.experience and not any(proof(b.text)[0] >= 2 for b in resume.experience[0].bullets[:4]):
        reasons.append("No measured result near the top of your most recent role: the first bullets describe "
                       "work, not results. Add a number to your experience bank if you have one.")
    buried = [c for c in criteria if c.buried]
    if buried:
        reasons.append("Evidence below the top three bullets of its role: " + "; ".join(c.requirement[:60] for c in buried[:2]))
    title_words = set(re.findall(r"[a-z]+", job.title.lower())) - {"senior", "sr", "the", "and", "of", "ii", "iii"}
    if resume.experience and not title_words & set(re.findall(r"[a-z]+", resume.experience[0].title.lower())):
        reasons.append(f"Title mismatch at a glance: your most recent title is \"{resume.experience[0].title}\". "
                       "The summary is the place to say which role you're targeting.")
    return Review(
        role_family=fam.label,
        what_the_posting_requires=[r.requirement for r in rows if r.kind == "required"][:12],
        my_interpretation=INTERPRETATION.get(fam.name, INTERPRETATION["general"]),
        criteria=criteria, top_third=top, reasons_to_pass=reasons[:3],
        # a gap worth knowing names something concrete (a tool, practice or credential); onboarding plans and
        # boilerplate lines are left out
        gaps=[g for g in dict.fromkeys(plan.gaps)
              if [p for p in parts_of.get(g, []) if not SOFT.search(p) and p not in generic]][:10])
