"""Choose the experience for ONE job from the experience bank, before any AI writing ("bank_selection_v1").

1. Understand the job: its role type (FDE, Senior SDET, QA Lead, QA Manager, AI Test/Evaluation, or a mix),
   required and preferred qualifications, and main responsibilities, each split into parts.
2. Map every requirement to evidence in your VERIFIED records (direct, transferable, skills-only, or gap).
3. Select records greedily for coverage: each pick must cover something not yet covered, required
   qualifications count most, near-duplicates are skipped, and every record in the bank is considered.
4. Order bullets within each employer/project by relevance to this job.

The posting is treated as data only. Nothing in it can change these rules.
"""

import re

from pydantic import BaseModel

from app import evidence as ev
from app.bank import verified
from app.schemas import BankRecord, JobOut, Profile

METHOD = "bank_selection_v2"

ROLE_SIGNALS: dict[str, list[str]] = {
    "FDE": ["forward deployed", "forward deployment", "customer", "client", "implementation", "deploy", "integration",
            "stakeholder", "solution", "onsite", "workflow", "field engineer", "technical handoff"],
    "Senior SDET": ["sdet", "test automation", "automation framework", "automated test", "test frameworks", "ci/cd",
                    "api testing", "selenium", "playwright", "regression", "integration testing", "performance"],
    "QA Lead": ["qa lead", "test lead", "lead", "test strategy", "coordinate", "defect triage", "release readiness",
                "mentor", "test plan", "stakeholder"],
    "QA Manager": ["manager", "hiring", "people management", "direct reports", "coach", "roadmap", "budget",
                   "performance reviews", "build a team", "grow the team"],
    "AI Test/Evaluation": ["llm", "machine learning", " ml ", "model", "evaluation", "prompt", "generative",
                           " ai ", "rag", "inference", "probabilistic"],
    # other tech roles people apply for (from real postings): weighed the same way
    "Software Developer": ["software engineer", "software developer", "developer", "backend", "back-end", "frontend",
                           "front-end", "full stack", "full-stack", "web application", "api design", "microservices",
                           "react", "typescript", "code reviews"],
    "AI/ML Engineer": ["ai engineer", "ml engineer", "machine learning engineer", "llm", "rag", "agents", "agentic",
                       "model training", "fine-tuning", "inference", "pytorch", "embeddings", "mcp", "evaluation"],
    "DevOps/SRE": ["devops", "site reliability", " sre", "platform engineer", "infrastructure", "terraform",
                   "kubernetes", "observability", "on-call", "slo", "incident", "ci/cd"],
    "Data Engineer": ["data engineer", "data pipeline", "etl", "spark", "airflow", "dbt", "data warehouse",
                      "snowflake", "databricks", "data model"],
    "Solutions Engineer": ["solutions engineer", "solutions architect", "sales engineer", "pre-sales", "presales",
                           "technical discovery", "proof of concept", "demo", "customer", "stakeholder"],
}

# Per-user rules live in the profile (profile.resume_settings): bullets per role, record priorities per role
# type, and which themes the most recent role should lean towards. See app/schemas.py ResumeSettings.

# Extra words that show a posting stresses a theme (your bullets are matched on FOCUS_THEMES only).
POSTING_FOCUS_EXTRA: dict[str, tuple[str, ...]] = {
    "ai": ("ml", "machine learning", "model", "models", "generative", "genai", "agents", "inference"),
}

# Themes the most recent role can lean towards (chosen in resume_settings.current_role_focus).
FOCUS_THEMES: dict[str, tuple[str, ...]] = {
    "automation": ("automation", "automated", "framework", "playwright", "selenium", "rest assured", "cucumber",
                   "junit", "regression", "api", "test suite", "pytest"),
    "cloud": ("kubernetes", "helm", "cloud", "aws", "azure", "docker", "microservices", "linkerd", "deployment",
              "upgrade", "pod", "ci/cd", "jenkins", "pipeline"),
    "ai": ("ai", "copilot", "claude", "llm", "ai-generated", "ai-assisted"),
    "monitoring": ("opensearch", "prometheus", "grafana", "monitoring", "observability", "logs", "log",
                   "dashboards", "metrics", "latency", "k6", "performance", "diagnostic"),
    "data": ("sql", "etl", "data pipeline", "data pipelines", "databricks", "spark", "pyspark", "warehouse",
             "data quality", "reconciliation", "airflow", "dbt"),
    "leadership": ("led", "lead", "mentored", "mentoring", "coordinated", "stakeholders", "strategy", "planning",
                   "managed", "hiring", "roadmap"),
    "performance": ("performance", "load testing", "latency", "throughput", "k6", "jmeter", "gatling", "benchmark"),
    "security": ("security", "authentication", "authorization", "oauth", "owasp", "vulnerability", "penetration",
                 "encryption", "compliance"),
}
MAX_PROJECT = {"default": 6, "FDE": 9, "AI Test/Evaluation": 10, "AI/ML Engineer": 10, "Solutions Engineer": 8}
# The LLM work itself (not just tests that happen to use a fake model).
AI_CORE = re.compile(r"\b(llm|llm-generated|language models?|ollama|prompts?|prompt-regression|adversarial|"
                     r"generated (claims|content|responses|requirements)|fabricated|metamorphic|paraphrased)\b",
                     re.IGNORECASE)
AI_TERMS = re.compile(r"\b(llm|language models?|ollama|model|models|prompt|prompts|generated|agent|evaluation|"
                      r"claim|ai)\b", re.IGNORECASE)

WEIGHT = {"required": 3.0, "responsibility": 1.5, "preferred": 1.2}
# Requirements that are only soft skills ("clear communication", "take ownership") are real, but a recruiter
# looks for hard proof first: they count for less and never decide which bullet leads a role.
SOFT = re.compile(r"communicat|collaborat|stakeholder|ownership|ambigu|teamwork|team player|tradeoff|trade-off|"
                  r"autonom|independ|curious|curiosity|mindset|proactive|interpersonal|present|written|verbal|"
                  r"relationship|partner|empathy|adapt|fast-paced|self-starter|problem[- ]solving|detail|"
                  r"tenacious|pragmatic|expectations|async|remote|culture|values|passion|growth|feedback|"
                  r"high-trust|delivery-oriented|product-minded|ship|deliver", re.IGNORECASE)
SOFT_FACTOR = 0.35
DIRECT, TRANSFER = 1.0, 0.6


class Requirement(BaseModel):
    text: str
    kind: str                     # required | preferred | responsibility | eligibility
    parts: list[str]


class Row(BaseModel):
    requirement: str
    kind: str
    selected_ids: list[str]
    strength: str                 # direct | partial | transferable | skills only | gap
                                  # soft-skill lines: shown by example | not shown (never a gap)
    location: list[str]           # where in the resume: employer or project names
    not_found_parts: list[str] = []
    found_parts: list[str] = []   # what of the line your records show
    category: str = "technical"   # technical | soft skill | education
    found_elsewhere: dict[str, str] = {}   # part -> "skills list" / "experience bank (ID, not on this resume)"


class Plan(BaseModel):
    method: str = METHOD
    role_types: list[str]          # primary first
    role_scores: dict[str, int]
    requirements: list[Requirement]
    selected: dict[str, list[str]]           # owner -> record IDs in resume order
    rows: list[Row]
    gaps: list[str]
    excluded_by_status: list[str]            # "QM03 (unverified): matches 'capacity planning'"
    removed_as_duplicate: list[str]
    caveats: dict[str, str]                  # record ID -> context that must be kept
    eligibility: list[str]
    scores: dict[str, float] = {}            # record ID -> how much it matters for this job (for fitting the page)
    family: str = "general"                  # role family read from the posting (scorecard.family)
    why: dict[str, list[str]] = {}           # record ID -> what this role looks for that the bullet shows
    # the shared evidence selection for the resume summary AND the cover letter (tailoring.evidence_priorities):
    # [{ask, kind, match: direct | transferable | missing, records: [ids], main: bool}]
    priorities: list[dict] = []


def _in_range(rid: str, spec: str) -> bool:
    """rid in "AB01-AB08" (or equal to "AB03"). Malformed specs never match."""
    spec = spec.strip().upper()
    if "-" not in spec:
        return rid == spec
    a, _, b = spec.partition("-")
    ma, mb, m = (re.fullmatch(r"([A-Z]+)(\d+)", x.strip()) for x in (a, b, rid))
    if not (ma and mb and m) or not (m.group(1) == ma.group(1) == mb.group(1)):
        return False
    return int(ma.group(2)) <= int(m.group(2)) <= int(mb.group(2))


def classify(job: JobOut) -> tuple[list[str], dict[str, int]]:
    title = f" {job.title.lower()} "
    body = f" {job.description.lower()} "
    scores = {role: 3 * sum(title.count(s) for s in sig) + sum(body.count(s) for s in sig)
              for role, sig in ROLE_SIGNALS.items()}
    # the job title names the role most reliably: a clear role phrase in it outweighs words in the description
    title_roles = [
        (r"\b(ai|ml|llm|model|genai)\b.*\b(test|qa|quality|evaluation|evals?)\b|\b(evaluation|evals) engineer",
         "AI Test/Evaluation"),
        (r"forward[- ]deployed|\bfde\b", "FDE"),
        (r"\bsdet\b|engineer in test|test automation|automation (test )?engineer|\b(qa|quality|test) engineer", "Senior SDET"),
        (r"site reliability|\bsre\b|devops|platform engineer|infrastructure engineer|cloud engineer", "DevOps/SRE"),
        (r"data engineer|analytics engineer|etl", "Data Engineer"),
        (r"\b(ai|ml|machine learning|llm|applied ai) (engineer|scientist|developer)\b|\bai/ml\b", "AI/ML Engineer"),
        (r"solutions? (engineer|architect|consultant)|sales engineer|customer engineer", "Solutions Engineer"),
        (r"\b(software|backend|back-end|frontend|front-end|full[- ]?stack|web|mobile) (engineer|developer)\b|"
         r"\bdeveloper\b", "Software Developer"),
    ]
    for pattern, role in title_roles:
        if re.search(pattern, title) and not (role == "Software Developer" and "in test" in title):
            scores[role] += 30 if role == "AI Test/Evaluation" else 25
    qa_title = re.search(r"\b(qa|quality|test|testing|sdet)\b", title)
    if qa_title and re.search(r"\b(manager|head of|director)\b", title):
        scores["QA Manager"] += 6
    if qa_title and re.search(r"\blead\b", title):
        scores["QA Lead"] += 6
    best = max(scores.values()) or 1
    types = [r for r, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s >= 0.6 * best and s > 0]
    return types or ["Senior SDET"], scores


_NOT_A_REQUIREMENT = re.compile(
    r"salary|compensation|pay range|base pay|\bbonus\b|equity|benefits|pto\b|vacation|401k|insurance|"
    r"this posting|new vacancy|hiring process|^(begin|start)\s|^develop (a |an )?(strong |deep |good |in-depth )?understanding|"
    r"#\w|^apply (today|now)\b|\$\s?\d|\b\d{2,3},\d{3}\b|"
    r"^get to know|^learn\s|^meet\s|onboard|^you('ll| will) (learn|work alongside|join)\b|^you won'?t\b|"
    r"application experience|accommodation|equal opportunity|\beeo\b|privacy|"
    r"^(first|within|by|after)\s+\d+\s+days|^\d+\s+days|success outcomes|^what success looks like", re.IGNORECASE)


def requirements(job: JobOut, profile: Profile) -> list[Requirement]:
    vocab = ev.vocabulary(profile)
    req, pref = ev.posting_requirements(job.description)
    req = req or job.required_skills
    pref = pref or job.preferred_skills
    resp = ev.posting_responsibilities(job.description)
    out: list[Requirement] = []
    for kind, lines in (("required", req), ("preferred", pref), ("responsibility", resp)):
        for line in lines:
            if _NOT_A_REQUIREMENT.search(line) or (len(line.split()) <= 3 and not ev.requirement_parts(line, vocab)):
                continue                  # pay, benefits, "First 30 Days", stray headings: not something to prove
            if kind == "responsibility" and _about_the_company(line, job.company):
                continue                  # "Acme makes it easy to ...": the company describing itself, not a duty
            if kind != "responsibility" and re.search(r"authori[sz]ed to work|citizen|clearance|work permit|"
                                                      r"eligible to work|relocat|travel|english|french|bilingual|"
                                                      r"language proficiency", line, re.IGNORECASE):
                out.append(Requirement(text=line, kind="eligibility", parts=[]))
                continue
            out.append(Requirement(text=line, kind=kind, parts=ev.requirement_parts(line, vocab)))
    return out


_EDUCATION = re.compile(r"\b(education|degree|diploma|bachelor'?s?|master'?s?|undergraduate|post-secondary|"
                        r"graduate of|university)\b", re.IGNORECASE)
_LEAD_VERB = re.compile(r"^(leverage|leveraging|use|using|build|building|design|designing|develop|developing|implement|"
                        r"architect|maintain|drive|ensure|optimi[sz]e|enhance|integrate|identify|translate|estimate|"
                        r"plan|execute|deliver|support|perform|conduct|create|write|apply|own|lead|work closely|work|contribute to|"
                        r"contribute|assist with|assist|partner with|establish|define|languages?)\s+", re.IGNORECASE)
_ANY_OF = re.compile(r"\bor\b|such as|e\.g\.|for example|similar|\bany\b", re.IGNORECASE)
_ROLE_WORDS = re.compile(r"\b(you|your|this role|the role|will be|responsible|responsibilities|the (senior |junior |lead )?"
                         r"[\w-]+ (engineer|tester|developer|analyst|lead|manager))\b", re.IGNORECASE)


def _about_the_company(line: str, company: str) -> bool:
    """A sentence where the company describes itself ("Acme makes it easy...", "Working for a company like Acme
    means...", "At its heart, the Acme platform..."): it names the company and says nothing about the role."""
    names = [company.strip()] + [w for w in company.split()[:1] if len(w) >= 4]
    low = line.lower()
    return any(n and re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", low) for n in names) \
        and not _ROLE_WORDS.search(line)


# Parts that are a people skill or a way of working, never a technical one (on top of vocabulary.is_people_skill)
_NOT_SKILL = re.compile(r"\b(customers?|clients?|business analysts?|developers|teams?|quality standards|culture|"
                        r"timely|preferred|asset|plus|managers?|professionals|team members|engineers|partners?|"
                        r"sales|customer success|marketing|finance|go-to-market)\b", re.IGNORECASE)
# everyday words a posting capitalises in a list ("(Extract, Transform, Load)"); not product names
_COMMON_WORDS = {"extract", "transform", "load", "support", "operations", "product", "engineering", "design",
                 "quality", "success", "delivery", "security", "data", "development", "testing", "research"}


def technical_parts(q: Requirement, vocab: set[str]) -> list[str]:
    """The named technical skills a requirement line asks for: tools, languages, platforms and engineering
    practices ("RestAssured", "OOP principles", "end-to-end testing"). Verbs and ways of working ("architect",
    "independently design", "estimate"), people skills ("analytical", "problem-solving") and filler
    ("preferred", "asset") are not scored: a resume shows them by example, and they are never a gap."""
    from app.vocabulary import find_terms, is_people_skill
    out: list[str] = []
    for part in q.parts:
        part = ev.normalize(_LEAD_VERB.sub("", part).strip())
        if not part or part in out or ". " in part:
            continue
        if ev._degree_part(part) or is_people_skill(part) or _NOT_SKILL.search(part) or SOFT_PART.search(part):
            continue
        if part in vocab:
            out.append(part)
            continue
        named = [ev.normalize(n.lower()) for _, _, n in find_terms(part)]
        if named:
            # a longer phrase around a named skill ("dashboards in Power BI", "environment setup using GitHub
            # Actions"): the skill is what is scored
            out.extend(n for n in named if n not in out and not _NOT_SKILL.search(n))
            continue
        if len(part.split()) > 3 or part in _COMMON_WORDS:
            continue
        # a product name as the posting writes it ("Kafka", "HL7 v2", "EHR systems"): capitals or digits that are
        # not just the start of a sentence
        m = re.search(rf"(?<![\w]){re.escape(part)}(?![\w])", q.text, re.IGNORECASE)
        if m:
            word = q.text[m.start():m.end()]
            start = m.start() == 0 or q.text[:m.start()].rstrip().endswith((".", ":", ";"))
            if re.search(r"[A-Z]{2}|\d|[a-z][A-Z]", word) or (word[:1].isupper() and not start):
                out.append(part)
    return out


# people-skill words inside a part (SOFT minus words that also name technical things, like "async")
SOFT_PART = re.compile(r"communicat|collaborat|stakeholder|ownership|ambigu|teamwork|team player|autonom|independ|"
                       r"curios|mindset|proactive|interpersonal|relationship|empathy|fast-paced|self-starter|"
                       r"problem[- ]solving|analytic|detail-oriented|culture|passion", re.IGNORECASE)


def _is_soft(q: Requirement) -> bool:
    """A line about personality or ways of working ("curiosity, initiative", "seller-doer mindset, client
    relationships", "collaboration across time zones"): every part of it is a people skill, not a technical one."""
    from app.vocabulary import is_people_skill
    if q.parts:
        return all(SOFT.search(p) or is_people_skill(p) for p in q.parts)
    return bool(SOFT.search(q.text))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+#./-]*", text.lower()) if len(w) > 3}


_ACHIEVE_VERBS = {"built", "developed", "designed", "led", "created", "implemented", "investigated", "diagnosed",
                  "isolated", "debugged", "troubleshot", "reduced", "improved", "automated", "integrated", "migrated",
                  "established", "defined", "reproduced", "resolved", "validated", "configured", "deployed"}
_TALK_VERBS = {"documented", "presented", "communicated", "demonstrated", "shared", "participated", "attended",
               "worked", "collaborated", "contributed", "assisted", "supported", "helped"}
# everyday practice, not an achievement: fine as a bullet only when nothing stronger fits
_ROUTINE_WORK = re.compile(r"\b(feature branches|commits|merge requests|pull requests|stand-?ups?|tickets?|"
                           r"participated|attended)\b", re.IGNORECASE)


def internal_names(profile: Profile) -> dict[str, list[str]]:
    """Record ID -> the internal names in it that a reader outside your team can't decode: capitalised words or
    acronyms that are not a known tool, one of your skills or an employer, and that appear in at most two of your
    records ("CIX", "Harvester, Collector, Gateway, OpenDocs"). Names you use often ("Enterprise Imaging") are the
    product you worked on and count as known."""
    records = verified(profile)
    known = {t.lower() for t in KNOWN_TECH} | {x.lower() for x in profile.skills} | {
        x.lower() for g in profile.skill_groups for x in g.skills}
    known |= {w.lower() for e in profile.experience for w in re.findall(r"[A-Za-z&]+", e.company)}
    common = {"api", "apis", "ui", "ci", "cd", "qa", "sql", "etl", "aml", "llm", "ai", "json", "xml", "http", "zip",
              "pdf", "csv", "rpm", "url", "ha", "p95", "p99", "id", "ids", "uat", "sre", "fhir", "hl7", "gui", "utc",
              "dsl", "cpu", "crd", "bdd", "tdd", "ragas", "sdk", "cli", "jwt", "sso", "ldap", "dns", "tls", "ssl",
              "vm", "os", "ram", "gpu", "rest", "sns", "sqs", "gcp", "aws", "tm4j", "sox", "jvm",
              "greenhouse", "lever", "ashby", "langsmith", "langfuse"}     # public products, not internal names
    from app.vocabulary import find_terms

    def names(text: str) -> list[str]:
        covered = [(a, b) for a, b, _ in find_terms(text)]
        low = text.lower()
        for t in known:                  # inside a tool or skill name ("REST Assured", "Spring Boot")
            for m in re.finditer(rf"(?<![\w-]){re.escape(t)}(?![\w-])", low):
                covered.append((m.start(), m.end()))
        # a run of two or more capitalised words is a proper name ("Enterprise Imaging", "Legendary Award")
        for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+(?:of\s+)?[A-Z][a-z]+)+\b", text):
            covered.append((m.start(), m.end()))
        found = []
        for m in re.finditer(r"(?<![\w-])([A-Z][a-z]+[A-Z]\w*|[A-Z]{2,6}|[A-Z][a-z]{3,})(?![\w-])", text):
            w = m.group(1)
            if m.start() == 0 or text[:m.start()].rstrip().endswith((".", ":")):
                continue                # the first word of a sentence
            if w.lower() in known or w.lower() in common or any(a <= m.start() < b for a, b in covered):
                continue
            found.append(w)
        return found
    freq: dict[str, int] = {}
    per = {r.id: names(r.text) for r in records}
    for ws in per.values():
        for w in set(ws):
            freq[w] = freq.get(w, 0) + 1
    out = {}
    for rid, ws in per.items():
        rare = [w for w in dict.fromkeys(ws) if freq[w] <= 2]
        if len(rare) >= 2 or any(w.isupper() for w in rare):
            out[rid] = rare
    return out


def plan(profile: Profile, job: JobOut) -> Plan:
    from app import scorecard
    fam = scorecard.family(job.title, job.description)
    role_types, role_scores = classify(job)
    reqs = requirements(job, profile)
    soft = {qi for qi, q in enumerate(reqs) if _is_soft(q)}

    def w(qi: int, part: str = "") -> float:
        is_soft = qi in soft or bool(part and SOFT.search(part))
        return WEIGHT.get(reqs[qi].kind, 1.0) * (SOFT_FACTOR if is_soft else 1.0)
    records = verified(profile)
    by_id = {r.id: r for r in records}
    vocab = ev.vocabulary(profile)
    order = {e.company: i for i, e in enumerate(profile.experience)}

    # --- evidence: requirement x record ---------------------------------------------------------------
    hits: dict[str, dict[int, dict[str, float]]] = {}       # rid -> req index -> {part: value}
    for r in records:
        for qi, q in enumerate(reqs):
            if q.kind == "eligibility":
                continue
            m = ev.match_requirement(profile, q.text, [(r.id, r.text)], vocab)
            if m.found:
                hits.setdefault(r.id, {})[qi] = {e.part: (TRANSFER if e.related else DIRECT) for e in m.evidence}

    # Broad parts ("automation", "testing") appear in many records and say little: weigh each part by how
    # rare it is in your bank, so a precise match beats a generic one.
    # A part counts once per bullet however many requirement lines repeat it ("automation" can appear in a
    # dozen lines of one posting), and by how rare it is across your bank.
    part_df: dict[str, int] = {}
    for parts_by_q in hits.values():
        for part in {p for parts in parts_by_q.values() for p in parts}:
            part_df[part] = part_df.get(part, 0) + 1
    n = max(1, len(records))
    part_rarity = {p: 1.0 / (1.0 + 4.0 * v / n) for p, v in part_df.items()}
    rarity = {(qi, part): part_rarity[part] for parts_by_q in hits.values() for qi, parts in parts_by_q.items()
              for part in parts}

    ai_job = bool(re.search(r"\b(ml|machine learning|llm|ai|model|models|generative|inference)\b",
                            f"{job.title} {job.description}", re.IGNORECASE))

    # how much the posting stresses each focus theme (0..1), with a floor so the focus always applies a little
    posting = f" {job.title} {job.description} ".lower()
    stress = {}
    settings = profile.resume_settings
    focus_themes = {t: FOCUS_THEMES[t] for t in settings.current_role_focus if t in FOCUS_THEMES}
    max_per_role, min_per_role = settings.max_bullets or [10], settings.min_bullets or [0]
    for theme, words in focus_themes.items():
        words = words + POSTING_FOCUS_EXTRA.get(theme, ())
        stress[theme] = sum(len(re.findall(rf"(?<![\w]){re.escape(w)}(?![\w])", posting)) for w in words)
    top = max(stress.values(), default=0) or 1
    stress = {t: 0.25 + 0.75 * v / top for t, v in stress.items()}
    current = profile.experience[0].company if profile.experience else None

    def focus(r: BankRecord) -> float:
        if r.owner != current:
            return 0.0
        low = f" {r.text.lower()} "
        return sum(w for t, w in stress.items()
                   if any(re.search(rf"(?<![\w]){re.escape(x)}(?![\w])", low) for x in focus_themes[t]))

    design_job = bool(re.search(r"\b(system design|technical design|solution design|design reviews?|"
                                r"solutions? architect|software architect)\b", job.description, re.IGNORECASE))

    posting_low = f" {job.title} {job.description} ".lower()
    role_pts = {r.id: scorecard.role_points(fam, r.text, posting_low) for r in records}
    quality = {r.id: scorecard.strength(r.text) for r in records}
    lead_role = next((e.company for e in profile.experience if re.search(r"\blead\b", e.title.lower())), None)

    def boost(rid: str) -> float:
        b = 0.0
        r = by_id.get(rid)
        # what a hiring manager for this kind of role looks for first, and how convincing the bullet is
        b += 0.6 * role_pts.get(rid, (0.0, []))[0] + 0.8 * quality.get(rid, 0.0)
        if r is not None and r.caveat and re.search(r"design", r.caveat, re.IGNORECASE) and not design_job:
            b -= 3.0                   # design-only work counts less unless the job is about design
        if r is not None:
            b += 2.0 * focus(r)        # most recent role: automation, cloud, AI and monitoring, as the job stresses them
        if ai_job and r is not None and r.owner_kind == "project" and AI_CORE.search(r.text):
            b += 8.0                    # AI/ML job: the LLM work in your project comes first
        for rank, role in enumerate(role_types[:2]):
            if any(_in_range(rid, spec) for spec in settings.role_priority.get(role, [])):
                b += 1.5 if rank == 0 else 0.75
        return b

    lead_family = fam.name in ("lead", "data_lead", "manager")
    internal = internal_names(profile)

    def action(rid: str) -> float:
        """How much a bullet shows you DOING the work: what you built, led, tested or diagnosed (and a measured
        result) counts more than a matching tool name; talking about work and routine practice count less."""
        r = by_id.get(rid)
        if r is None:
            return 0.0
        verb = scorecard.first_verb(r.text)
        v = 0.5 * scorecard.proof(r.text)[0]
        if verb in _ACHIEVE_VERBS:
            v += 1.5
        elif verb in _TALK_VERBS:
            v -= 0.5 if lead_family else 2.5
        if _ROUTINE_WORK.search(r.text):
            v -= 3.0
        if internal.get(rid):
            v -= 3.0                   # internal system names a recruiter can't read ("CIX", "Harvester")
        return v

    def relevance(rid: str) -> float:
        best: dict[str, float] = {}
        for qi, parts in hits.get(rid, {}).items():
            for part, v in parts.items():
                best[part] = max(best.get(part, 0.0), w(qi, part) * v * part_rarity[part])
        lines = len([qi for qi in hits.get(rid, {}) if qi not in soft])
        return sum(best.values()) + 0.1 * lines

    def cap(r: BankRecord) -> int:
        if r.owner_kind == "project":
            return next((MAX_PROJECT[t] for t in role_types[:1] if t in MAX_PROJECT), MAX_PROJECT["default"])
        base = max_per_role[min(order.get(r.owner, 99), len(max_per_role) - 1)]
        if fam.name in ("lead", "data_lead", "manager") and r.owner == lead_role:
            base += 2                       # lead roles: your test-lead experience carries more of the story
        return base

    # --- greedy selection for coverage --------------------------------------------------------------
    covered: dict[tuple[int, str], float] = {}
    chosen: list[str] = []
    per_owner: dict[str, int] = {}
    duplicates: list[str] = []

    def too_similar(r: BankRecord) -> str | None:
        w = _words(r.text)
        for cid in chosen:
            c = by_id[cid]
            if c.owner != r.owner:
                continue
            cw = _words(c.text)
            if w and cw and len(w & cw) / len(w | cw) > 0.45:
                return cid
        return None

    def gain(rid: str) -> float:
        best: dict[str, float] = {}
        new_lines = 0
        for qi, parts in hits.get(rid, {}).items():
            fresh = False
            for part, v in parts.items():
                add = w(qi, part) * part_rarity[part] * max(0.0, v - covered.get((qi, part), 0.0))
                best[part] = max(best.get(part, 0.0), add)
                fresh = fresh or add > 0
            new_lines += fresh and qi not in soft
        return sum(best.values()) + 0.15 * new_lines

    candidates = [r.id for r in records if r.id in hits]
    while True:
        best, best_score = None, 0.0
        for rid in candidates:
            if rid in chosen or per_owner.get(by_id[rid].owner, 0) >= cap(by_id[rid]):
                continue
            g = gain(rid)
            if g <= 0:
                continue
            score = g + 0.1 * relevance(rid) + 0.3 * boost(rid)
            if score > best_score:
                best, best_score = rid, score
        if best is None:
            break
        dup = too_similar(by_id[best])
        if dup:
            duplicates.append(f"{best} (too close to {dup})")
            candidates.remove(best)
            continue
        chosen.append(best)
        per_owner[by_id[best].owner] = per_owner.get(by_id[best].owner, 0) + 1
        for qi, parts in hits[best].items():
            for part, v in parts.items():
                covered[(qi, part)] = max(covered.get((qi, part), 0.0), v)

    # --- depth: also keep strongly relevant records (distinct evidence of what the job stresses) -------------
    if chosen:
        bar = 0.5 * max(relevance(c) for c in chosen)
        for rid in sorted(candidates, key=lambda x: -(relevance(x) + boost(x))):
            r = by_id[rid]
            if rid in chosen or relevance(rid) + boost(rid) < bar or per_owner.get(r.owner, 0) >= cap(r):
                continue
            dup = too_similar(r)
            if dup:
                duplicates.append(f"{rid} (too close to {dup})")
                continue
            chosen.append(rid)
            per_owner[r.owner] = per_owner.get(r.owner, 0) + 1

    # --- every role stays understandable: top up to a minimum, most relevant first ----------------------
    project_min = 7 if ({"FDE", "AI Test/Evaluation", "AI/ML Engineer"} & set(role_types[:2]) or ai_job) else 4
    places = dict(order)
    places.update({pr.name: None for pr in profile.projects})
    for company, idx in places.items():
        minimum = project_min if idx is None else min_per_role[min(idx, len(min_per_role) - 1)]
        pool = sorted((r for r in records if r.owner == company and r.id not in chosen),
                      key=lambda r: -(relevance(r.id) + boost(r.id)))
        for r in pool:
            if per_owner.get(company, 0) >= minimum:
                break
            if too_similar(r):
                continue
            chosen.append(r.id)
            per_owner[company] = per_owner.get(company, 0) + 1

    # --- most recent role: at least one bullet for each focus theme the posting stresses ---------------------
    protected: set[str] = set()
    if current:
        for theme, weight in sorted(stress.items(), key=lambda kv: -kv[1]):
            if weight < 0.5:
                continue
            words = focus_themes[theme]
            matches = lambda r: any(re.search(rf"(?<![\w]){re.escape(x)}(?![\w])", f" {r.text.lower()} ")
                                    for x in words)
            have = [rid for rid in chosen if by_id[rid].owner == current and matches(by_id[rid])]
            if have:
                protected.add(max(have, key=lambda x: relevance(x) + boost(x)))
                continue
            pool = sorted((r for r in records if r.owner == current and r.id not in chosen and matches(r)),
                          key=lambda r: -(relevance(r.id) + boost(r.id)))
            for r in pool:
                if not too_similar(r):
                    chosen.append(r.id)
                    per_owner[current] = per_owner.get(current, 0) + 1
                    protected.add(r.id)
                    break

    # --- AI/ML job: the project section leads with the LLM work (at least 5 such records if you have them) ---
    if ai_job:
        for pr in profile.projects:
            ai_records = [r for r in records if r.owner == pr.name and AI_CORE.search(r.text)]
            have = [r for r in ai_records if r.id in chosen]
            for r in sorted((r for r in ai_records if r.id not in chosen), key=lambda r: -relevance(r.id)):
                if len(have) >= 8 or per_owner.get(pr.name, 0) >= cap(r):
                    break
                if too_similar(r):
                    continue
                chosen.append(r.id)
                have.append(r)
                per_owner[pr.name] = per_owner.get(pr.name, 0) + 1

    # --- order within each employer/project ------------------------------------------------------
    selected: dict[str, list[str]] = {}
    position = {r.id: i for i, r in enumerate(profile.experience_bank)}
    def core_first(rid: str) -> int:        # AI/ML job: in the project, LLM work before test plumbing
        r = by_id[rid]
        return 0 if (ai_job and r.owner_kind == "project" and AI_CORE.search(r.text)) else 1

    def must_direct(rid: str) -> int:       # direct evidence of a required qualification leads its role
        # a specific match (a part that is not in a large share of your bullets) to a hard required line
        # (a technical part: "maintainability" in "... with a focus on quality and maintainability" is not one)
        return int(any(reqs[qi].kind == "required" and qi not in soft and not SOFT.search(part) and v == DIRECT
                       and part_rarity[part] >= 0.5 and part in tech_parts[qi]
                       for qi, parts in hits.get(rid, {}).items() for part, v in parts.items()))

    tech_parts = {qi: set(technical_parts(q, vocab)) | {p for p in q.parts if p in ev.RELATED and not SOFT.search(p)}
                  for qi, q in enumerate(reqs)}
    value = {rid: relevance(rid) + boost(rid) + action(rid) for rid in chosen}
    for rid in sorted(chosen, key=lambda x: (core_first(x), -(4 * must_direct(x) + value[x]), position[x])):
        selected.setdefault(by_id[rid].owner, []).append(rid)

    # each bullet a distinct purpose: one that proves only what a stronger bullet in the same role already proves
    # (the same specific requirement parts) goes to the end of its role, and the page fitting trims it before a
    # relevant achievement from an older role
    def proves(rid: str) -> set[tuple[int, str]]:
        return {(qi, part) for qi, parts in hits.get(rid, {}).items() if qi not in soft
                for part in parts if part_rarity.get(part, 0) >= 0.5 and not SOFT.search(part)}
    redundant: set[str] = set()
    for owner, ids in selected.items():
        seen: set[tuple[int, str]] = set()
        for rid in ids:
            mine = proves(rid)
            if mine and mine <= seen and rid not in protected:
                redundant.add(rid)
            seen |= mine
        selected[owner] = [i for i in ids if i not in redundant] + [i for i in ids if i in redundant]
    for rid in redundant:
        value[rid] -= 2.5

    # --- report --------------------------------------------------------------------------------
    bank_texts = [(r.id, r.text) for r in records]
    skill_texts = [("skills", ", ".join(profile.skills + [s for g in profile.skill_groups for s in g.skills]
                                         + [s for e in profile.experience for s in e.skills]
                                         + [s for pr in profile.projects for s in pr.skills]))]
    unverified = [r for r in profile.experience_bank if r.status != "verified"]
    rows: list[Row] = []
    gaps: list[str] = []
    excluded: list[str] = []
    eligibility: list[str] = []
    for qi, q in enumerate(reqs):
        if q.kind == "eligibility":
            eligibility.append(f"{q.text} -> your profile: {profile.preferences.work_authorization or 'not stated'}"
                               f"; location {profile.contact.location or 'not stated'}. Check this yourself.")
            continue
        ids = [rid for rid in chosen if qi in hits.get(rid, {})]
        if any(ev._degree_part(part) for part in q.parts) or _EDUCATION.search(q.text):
            # a degree is shown by Education only. "Post-secondary / undergraduate education in X" is met by a
            # bachelor's degree; "or equivalent working experience" is met by your experience.
            m = ev.match_requirement(profile, q.text, [("education", f"{e.degree}, {e.institution}")
                                                       for e in profile.education], vocab)
            degree_found = any(ev._degree_part(f) for f in m.found)
            advanced = re.search(r"\b(master|ph\.?d|doctor)", q.text, re.IGNORECASE)
            if not degree_found and not advanced and profile.education:
                degree_found = True
            if not degree_found and re.search(r"equivalent (work(ing)? )?experience", q.text, re.IGNORECASE) \
                    and profile.experience:
                degree_found = True
            rows.append(Row(requirement=q.text, kind=q.kind, selected_ids=[],
                            strength="direct" if degree_found else "gap", category="education",
                            location=["Education"] if degree_found else [], not_found_parts=[]))
            if not degree_found:
                gaps.append(q.text)
            continue
        tech = technical_parts(q, vocab)
        if qi in soft or not tech:
            # people skills and ways of working: your records can show them by example, never a gap to close.
            # A duty with no technical skill in it and nothing that shows it is not reported at all.
            if q.kind == "responsibility" and not ids:
                continue
            from app.vocabulary import is_people_skill
            found = {p for rid in ids for p in hits[rid][qi] if is_people_skill(p) or SOFT_PART.search(p)
                     or p in ev.RELATED}
            rows.append(Row(requirement=q.text, kind=q.kind, selected_ids=ids, category="soft skill",
                            strength="shown by example" if ids else "not shown",
                            location=sorted({by_id[i].owner for i in ids}), found_parts=sorted(found),
                            not_found_parts=[]))
            continue
        # only the named technical skills of the line are scored; each is looked for in the bullets on this
        # resume, then anywhere in your verified bank, then in your skill lists
        found = {p for rid in ids for p in hits[rid][qi]}
        shown_here = lambda part: part in found or any(part in f or f in part for f in found)
        elsewhere: dict[str, str] = {}
        for part in tech:
            if shown_here(part):
                continue
            hit = ev.find_part(part, bank_texts)
            if hit:
                elsewhere[part] = f"experience bank: {hit.source} (not on this resume)"
            elif ev.find_part(part, skill_texts):
                elsewhere[part] = "your skills list"
        missing = [p for p in tech if not shown_here(p) and p not in elsewhere]
        # "Jenkins, Azure DevOps, or similar", "such as X, Y": one of them is enough
        any_of = bool(_ANY_OF.search(q.text))
        if ids:
            direct = any(v == DIRECT for rid in ids for v in hits[rid][qi].values())
            strength = ("direct" if not missing or any_of else "partial") if direct else "transferable"
        else:
            strength = "skills only" if len(missing) < len(tech) else "gap"
            if strength == "gap" and q.kind != "responsibility":
                gaps.append(q.text)
        rows.append(Row(requirement=q.text, kind=q.kind, selected_ids=ids, strength=strength,
                        location=sorted({by_id[i].owner for i in ids}), not_found_parts=missing[:6],
                        found_parts=sorted(f for f in found if f in tech or f in vocab and not SOFT_PART.search(f)
                                           and not _NOT_SKILL.search(f)), found_elsewhere=elsewhere))
        for r in unverified:
            if ev.match_requirement(profile, q.text, [(r.id, r.text)], vocab).found and not ids:
                excluded.append(f"{r.id} ({r.status.replace('_', ' ')}): could show '{q.text[:60]}'")

    return Plan(
        role_types=role_types, role_scores=role_scores, requirements=reqs, selected=selected, rows=rows,
        gaps=gaps, excluded_by_status=list(dict.fromkeys(excluded)), removed_as_duplicate=duplicates,
        caveats={rid: by_id[rid].caveat for rid in chosen if by_id[rid].caveat}, eligibility=eligibility,
        scores={rid: round(value[rid] + (50 if rid in protected else 0), 2) for rid in chosen},
        family=fam.name, why={rid: role_pts[rid][1] for rid in chosen if role_pts.get(rid, (0, []))[1]},
    )


def named_skills(profile: Profile, p: Plan) -> list[str]:
    """The tools and skills this posting names that you have (e.g. "rest assured", "mcp"), however the posting
    spelled them: kept when the resume is trimmed to fit, like the skills the posting lists."""
    from app.vocabulary import TOOLS
    yours = {ev.normalize(x) for x in profile.skills + [s for g in profile.skill_groups for s in g.skills]}
    tools = {t.lower() for t in TOOLS} | {t.lower() for t in KNOWN_TECH}
    out: list[str] = []
    for r in p.rows:
        if r.category != "technical":
            continue
        for part in r.found_parts + list(r.found_elsewhere):
            if (part in tools or part in yours) and part not in out:
                out.append(part)
    return out


def working_profile(profile: Profile, p: Plan) -> Profile:
    """The profile as this job's resume should see it: each employer's and project's bullets are the selected
    records, in order. IDs stay in the plan, never in the text."""
    by_id = {r.id: r for r in profile.experience_bank}
    exps = []
    for e in profile.experience:
        ids = p.selected.get(e.company, [])
        exps.append(e.model_copy(update={"highlights": [by_id[i].text for i in ids] or e.highlights}))
    projects = []
    for pr in profile.projects:
        ids = p.selected.get(pr.name, [])
        projects.append(pr.model_copy(update={"highlights": [by_id[i].text for i in ids]}))
    return profile.model_copy(update={"experience": exps, "projects": projects})


# Tools that may be named inside your experience records without being in your skill lists. Only those that
# appear in a VERIFIED record are added, so everything shown is something you used.
KNOWN_TECH = [
    "Java", "Python", "Go", "Scala", "SQL", "TypeScript", "JavaScript", "Groovy", "Bash", "PySpark", "pandas",
    "Spring Boot", "Spring Cloud", "Angular", "FastAPI", "SQLite", "Playwright", "Selenium WebDriver", "Selenium",
    "REST Assured", "Cucumber", "JUnit 5", "JUnit", "TestNG", "pytest", "Mockito", "Awaitility", "Appium", "UFT",
    "Postman", "k6", "Ginkgo", "Gomega", "client-go", "Pact", "Testcontainers", "kind", "Vitest", "Allure",
    "Extent Reports", "Kubernetes", "Helm", "Docker", "Rancher", "RKE2", "AWS", "AWS EKS", "Azure",
    "Azure Data Factory", "Databricks", "Linkerd", "Traefik", "nginx", "RabbitMQ", "Redis", "PostgreSQL", "MySQL",
    "Oracle", "OpenSearch", "OpenSearch Dashboards", "Prometheus", "Grafana", "Fluent Bit", "Liquibase", "NFS",
    "Jenkins", "Jenkins DSL", "Gradle", "Maven", "Git", "GitHub", "GitHub Actions", "Bitbucket", "SVN",
    "Azure DevOps", "Jira", "AutoSys", "Hadoop", "Power BI", "Tableau", "HP ALM", "Ollama", "Great Expectations",
    "FHIR", "HL7", "GitHub Copilot", "Claude Code", "Linux",
]


# Where an extra tool goes: the first of your skill groups whose name contains one of the words, tried in order.
TECH_CATEGORY: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("automation", "testing"), ("pytest", "junit", "testng", "selenium", "selenium webdriver", "playwright", "appium",
                                 "uft", "mockito", "awaitility", "ginkgo", "gomega", "pact", "testcontainers", "vitest",
                                 "allure", "extent reports", "cucumber", "rest assured", "api testing",
                                 "integration testing", "rest apis", "debugging failures", "debug failures")),
    (("framework", "language"), ("spring boot", "spring cloud", "angular", "fastapi")),
    (("language", "framework"), ("java", "python", "go", "scala", "sql", "typescript", "javascript", "groovy", "bash",
                                 "pyspark", "pandas")),
    (("infrastructure", "cloud"), ("linkerd", "traefik", "nginx", "rabbitmq", "redis", "nfs")),
    (("cloud", "infrastructure"), ("kubernetes", "helm", "docker", "rancher", "rke2", "aws", "aws eks", "azure",
                                   "client-go", "kind", "linux")),
    (("ci/cd", "build", "tools"), ("jenkins", "jenkins dsl", "gradle", "maven", "git", "github", "github actions",
                                   "bitbucket", "svn", "azure devops", "autosys")),
    (("observability", "monitor", "performance", "data"), ("prometheus", "grafana", "opensearch",
                                                            "opensearch dashboards", "fluent bit", "k6")),
    (("data",), ("mysql", "oracle", "postgresql", "sqlite", "liquibase", "databricks", "azure data factory",
                 "great expectations", "hadoop", "power bi", "tableau", "fhir", "hl7")),
    (("ai applications", "ai"), ("ollama", "llm output validation")),
    (("ai-assisted", "ai"), ("github copilot", "claude code", "mcp", "prompt engineering")),
    (("tools", "practices", "leadership"), ("hp alm", "jira", "postman")),
]


def _category_group(skill: str, group_names: list[str]) -> str | None:
    low = skill.lower()
    for words, techs in TECH_CATEGORY:
        if low in techs:
            for word in words:
                hit = next((g for g in group_names if word in g.lower()), None)
                if hit:
                    return hit
            return None
    return None


# Which skill headings matter for which kind of role (words looked for in your heading names).
ROLE_HEADINGS: dict[str, tuple[str, ...]] = {
    "FDE": ("integration", "ai applications", "delivery", "cloud", "language", "framework", "data"),
    "AI Test/Evaluation": ("ai applications", "automation", "testing", "language", "observability", "data"),
    "Senior SDET": ("automation", "testing", "cloud", "ci/cd", "language", "performance", "framework"),
    "QA Lead": ("leadership", "quality", "automation", "testing", "delivery", "tools", "ci/cd"),
    "QA Manager": ("leadership", "quality", "delivery", "tools", "automation", "testing"),
    "Software Developer": ("language", "framework", "cloud", "ci/cd", "data", "automation", "tools"),
    "AI/ML Engineer": ("ai", "language", "data", "cloud", "automation", "observability"),
    "DevOps/SRE": ("cloud", "ci/cd", "observability", "monitoring", "automation", "language"),
    "Data Engineer": ("data", "language", "cloud", "ci/cd", "automation"),
    "Solutions Engineer": ("integration", "ai", "cloud", "leadership", "language", "delivery"),
}
MAX_HEADINGS = 7


def select_skills(profile: Profile, job: JobOut, p: Plan, limit: int = 0,
                  max_headings: int = MAX_HEADINGS) -> tuple[list[str], list[dict]]:
    """Every technology and skill you have actually used, with what this job asks for first.

    Sources: your skill lists, the skills per employer and project, and tools named in your VERIFIED experience
    records. Nothing is added because the job mentions it. ``limit`` > 0 caps the list (0 = no cap).
    """
    by_id = {r.id: r for r in profile.experience_bank}
    chosen_text = " ".join(by_id[i].text.lower() for ids in p.selected.values() for i in ids)
    verified_text = " ".join(r.text.lower() for r in verified(profile))
    weights = {"required": 0, "responsibility": 1, "preferred": 2}

    candidates = (profile.skills + [s for g in profile.skill_groups for s in g.skills]
                  + [s for e in profile.experience for s in e.skills]
                  + [s for pr in profile.projects for s in pr.skills]
                  + [t for t in KNOWN_TECH if verified_text and ev._has(verified_text, t.lower())])
    seen: set[str] = set()
    all_skills: list[str] = []
    for item in candidates:
        if item.lower() not in seen:
            seen.add(item.lower())
            all_skills.append(item)

    # the same skill written two ways: keep the more specific form only
    lower = {x.lower() for x in all_skills}
    covered_by = {"junit": "junit 5", "selenium": "selenium webdriver", "integration testing": "api and integration testing",
                  "debugging failures": "debug failures", "rest apis": "rest assured", "cucumber": "cucumber/bdd",
                  "redis": "redis ha"}
    all_skills = [x for x in all_skills if covered_by.get(x.lower()) not in lower]

    vocab = ev.vocabulary(profile)
    rank: dict[str, tuple[int, int]] = {}
    for i, skill in enumerate(all_skills):
        best = 9
        for q in p.requirements:
            if q.kind in weights and best > weights[q.kind] and \
                    ev.match_requirement(profile, q.text, [("s", skill)], vocab).found:
                best = weights[q.kind]
        if best == 9 and ev._has(chosen_text, skill.lower()):
            best = 5                       # used in the bullets on this resume
        rank[skill] = (best, i)
    ordered = sorted(all_skills, key=lambda s_: rank[s_])
    if limit:
        ordered = ordered[:limit]
    keep = {s_.lower() for s_ in ordered}

    groups, grouped = [], set()
    for g in profile.skill_groups:
        items = sorted((s_ for s_ in dict.fromkeys(g.skills) if s_.lower() in keep and s_.lower() not in grouped),
                       key=lambda s_: rank.get(s_, (9, 999)))
        grouped.update(s_.lower() for s_ in items)
        if items:
            groups.append({"name": g.name, "skills": items, "_rank": rank.get(items[0], (9, 999))})
    names = [g["name"] for g in groups]
    rest = []
    for s_ in ordered:
        if s_.lower() in grouped:
            continue
        home = _category_group(s_, names)
        if home:
            target = next(g for g in groups if g["name"] == home)
            target["skills"].append(s_)
            grouped.add(s_.lower())
        elif s_.lower() in {t.lower() for t in KNOWN_TECH} or not groups or rank[s_][0] <= 2:
            rest.append(s_)              # a named tool with no fitting heading: listed under "Other tools"
    for g in groups:
        g["skills"].sort(key=lambda s_: rank.get(s_, (9, 999)))
        # headings with the most skills this job asks for come first
        g["_rank"] = (-sum(1 for s_ in g["skills"] if rank.get(s_, (9,))[0] <= 2),
                      -sum(1 for s_ in g["skills"] if rank.get(s_, (9,))[0] <= 5), rank.get(g["skills"][0], (9, 999)))
    if rest:
        groups.append({"name": "Other tools and technologies" if groups else "Skills", "skills": rest,
                       "_rank": (1, 1, (10, 0))})
    groups.sort(key=lambda g: g["_rank"])
    groups = _pick_headings(groups, p, rank, max_headings)
    for g in groups:
        g.pop("_rank", None)
    shown = {s_.lower() for g in groups for s_ in g["skills"]}
    return [s_ for s_ in ordered if s_.lower() in shown], groups


def _is_tool(skill: str) -> bool:
    return skill.lower() in {t.lower() for t in KNOWN_TECH}


def _pick_headings(groups: list[dict], p: Plan, rank: dict, max_headings: int) -> list[dict]:
    """Keep at most `max_headings` headings: the ones this job and this kind of role need most.

    Named tools from a dropped heading move to a kept heading where they fit, so little tech is lost.
    Within a heading: what the job asks for, then named tools, then the rest.
    """
    if len(groups) <= max_headings:
        chosen, dropped = groups, []
    else:
        def score(g: dict) -> float:
            job_hits = sum(1 for s_ in g["skills"] if rank.get(s_, (9,))[0] <= 2)
            used_hits = sum(1 for s_ in g["skills"] if rank.get(s_, (9,))[0] <= 5)
            name = g["name"].lower()
            role_bonus = 0.0
            for i, role in enumerate(p.role_types[:2]):
                if any(w in name for w in ROLE_HEADINGS.get(role, ())):
                    role_bonus += 6 if i == 0 else 3
            tools = sum(1 for s_ in g["skills"] if _is_tool(s_))
            return 3 * job_hits + used_hits + role_bonus + 0.3 * tools
        ranked = sorted(groups, key=score, reverse=True)
        # the two headings most typical of this kind of role always make the cut (e.g. AI for AI roles)
        must = []
        for word in ROLE_HEADINGS.get(p.role_types[0], ())[:2]:
            hit = next((g for g in ranked if word in g["name"].lower() and g not in must), None)
            if hit:
                must.append(hit)
        rest = [g for g in ranked if g not in must]
        chosen = must + rest[:max_headings - len(must)]
        dropped = [g for g in ranked if g not in chosen]
        chosen = [g for g in groups if g in chosen]                       # keep the job-relevance order
    names = [g["name"] for g in chosen]
    present = {s_.lower() for g in chosen for s_ in g["skills"]}
    for g in dropped:
        for s_ in g["skills"]:
            if not _is_tool(s_) or s_.lower() in present:
                continue
            home = _category_group(s_, names) or next((n for n in names if "tool" in n.lower()), None)
            if home:
                next(c for c in chosen if c["name"] == home)["skills"].append(s_)
                present.add(s_.lower())
    for g in chosen:
        g["skills"].sort(key=lambda s_: (rank.get(s_, (9,))[0] > 2, not _is_tool(s_), rank.get(s_, (9, 999))))
    return chosen
