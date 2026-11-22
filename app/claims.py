"""Is each claim in a sentence backed by YOUR evidence? Shared by the resume summary, the cover letter and the
screening answers ("claims_v1").

A sentence may paraphrase and combine what your records show. It may not add
* a tool your profile doesn't show                                  (kind "tool")
* a number its records don't state                                  ("metric")
* an employer the work didn't happen at                             ("employer")
* leadership (led, managed, mentored ...) its records don't state    ("leadership")
* more ownership than its records ("built/designed" for "ran")      ("ownership")
* a result or benefit its records don't report                      ("outcome"): "reduced", "resolved",
  "made it easier to catch issues"; each kind of result must be one the records report
* an audience its records don't name ("customers" from "customer support")          ("audience")
* a responsibility its records don't state ("release readiness", "sign-off")      ("responsibility")
* a number without the unit or measure its record gives ("the same 2,048,000")    ("metric")
* a tool listed with work of another kind ("CI/CD pipelines using Jenkins and SQL") ("tool_context")
* the posting's duties in words your profile never uses             ("posting_duty", summary only: letters have
  their own grounding check)

Numbers, employers, leadership, ownership, results, audiences and responsibilities are checked against the
specific records the sentence was written from (or, for a letter or answer, the cited records plus the records
closest to it). Your own summary line is never evidence for them: it may back only years and industries.

"Your evidence" is the signed-in account's own profile: bullets, VERIFIED experience-bank records, projects and
skills. Each check returns [{kind, detail}]; nothing here calls the model.
"""

import re

from app import ats
from app.schemas import JobOut, Profile

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
NUMBER_WORDS = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|"
                          r"thirty|forty|fifty|hundreds?|thousands?|millions?|dozens?|twice|double|triple|half)\b",
                          re.IGNORECASE)
LEADERSHIP = re.compile(r"\b(led|leads?|leading|manag(?:ed|es|ing)|mentor(?:ed|ing|s)?|coach(?:ed|es|ing)|"
                        r"supervis\w*|head(?:ed|ing)? (?:of|up)|direct reports?|oversaw|oversee\w*|"
                        r"team of \d+|built (?:and led )?a team)\b", re.IGNORECASE)
# results, by kind: a sentence may claim a result only of a kind its records report
OUTCOME_KINDS = {
    "less": r"reduc\w*|cut(?:s|ting)?|decreas\w*|lower(?:ed|ing|s)?|shorten\w*|fewer|less|halv\w*|minimi[sz]\w*",
    "more": r"improv\w*|increas\w*|boost\w*|rais(?:ed|ing)|lift(?:ed|ing)|grew|grow\w*|doubl\w*|expand\w*|"
            r"strengthen\w*|enhanc\w*|optimi[sz]\w*",
    "faster": r"accelerat\w*|faster|sped up|speed(?:ing|s)? up|quicker",
    "fixed": r"resolv\w*|fix(?:ed|es|ing)|repair\w*|remediat\w*",
    "avoided": r"prevent\w*|eliminat\w*|avoid\w*|caught|catch(?:es|ing)?",
    "saved": r"sav(?:ed|es|ing)",
    # a benefit said in so many words: "which made it easier to ...", "so that teams could ..."
    "benefit": r"made it (?:easier|possible|faster|simpler)|making it (?:easier|possible|faster|simpler)|"
               r"easier to|so that|which (?:made|meant|let|helped|allowed|enabled)|resulting in|led to|"
               r"allow(?:ed|ing) (?:the )?teams? to|enabl(?:ed|ing) (?:the )?teams? to|giving (?:the )?teams?|"
               r"(?:this|that|which|it) (?:helped|ensured|made|meant|allowed|enabled|let|kept)|"
               r"help(?:ed|ing|s)? (?:to )?(?:ensure|keep|make|reduce|improve|catch|identify|speed)|"
               r"(?:to|and) ensure (?:accuracy|reliability|quality|consistency|stability|reliable|consistent|"
               r"repeatable|that)",
}
OUTCOMES = re.compile(r"\b(" + "|".join(OUTCOME_KINDS.values()) + r")\b", re.IGNORECASE)
KINDS = ("tool", "metric", "employer", "leadership", "ownership", "outcome", "audience", "responsibility",
         "tool_context", "posting_duty")

# who the work was for or with: a sentence may name an audience only if its records do
AUDIENCE = re.compile(r"\b(customers?|clients?|end[- ]users?|stakeholders?|executives?|partners?|vendors?|patients?|"
                      r"merchants?|business analysts?|product (?:managers?|owners?))\b", re.IGNORECASE)
# a team is not its audience: "customer support" are colleagues, not customers
_TEAM_NAMES = re.compile(r"\b(?:customer|client)[- ](?:support|service|success|care)s?\b", re.IGNORECASE)
# what the work was answerable for: a sentence may claim it only if its records state it
RESPONSIBILITY = re.compile(r"\b(release[- ]readiness|readiness decisions?|go/no-go|sign[- ]?offs?|responsible for|"
                            r"accountab\w+|on-call|roadmaps?|strateg(?:y|ies|ic)|budgets?|hiring)\b", re.IGNORECASE)

# present tense or "I" forms -> the past-tense verb the ownership scale knows
_PAST = {"build": "built", "builds": "built", "design": "designed", "designs": "designed", "lead": "led",
         "leads": "led", "own": "owned", "owns": "owned", "create": "created", "creates": "created",
         "architect": "architected", "define": "defined", "defines": "defined", "drive": "drove",
         "drives": "drove", "launch": "launched", "establish": "established", "introduce": "introduced"}


def outcome_kinds(text: str) -> set[str]:
    return {k for k, pat in OUTCOME_KINDS.items() if re.search(rf"\b(?:{pat})\b", text, re.IGNORECASE)}


def evidence_text(profile: Profile) -> str:
    """Everything your profile states as your own: bullets, verified records, projects, skills, own summary."""
    return " ".join([h for e in profile.experience for h in e.highlights]
                    + [r.text for r in profile.experience_bank if r.status == "verified"]
                    + [h for pr in profile.projects for h in (pr.highlights or [])]
                    + list(profile.skills) + [s for g in profile.skill_groups for s in g.skills]
                    + [profile.summary or "", profile.headline or ""])


def employer_forms(profile: Profile) -> dict[str, str]:
    """How each employer may be written in a sentence -> its name ("Contoso Health" and "Contoso")."""
    out: dict[str, str] = {}
    for e in profile.experience:
        out[e.company] = e.company
        first = e.company.split()[0].strip(",")
        if len(e.company.split()) > 1 and len(first) > 3:
            out.setdefault(first, e.company)
        plain = re.sub(r"\s*\(.*?\)\s*$", "", e.company).strip()        # "Example Corp (fictional)"
        if plain and plain != e.company:
            out.setdefault(plain, e.company)
    return out


def employers_named(sentence: str, profile: Profile) -> set[str]:
    return {name for form, name in employer_forms(profile).items()
            if re.search(rf"(?<!\w){re.escape(form)}(?!\w)", sentence)}


def owners_of(texts: list[str], profile: Profile) -> set[str]:
    """The employers / projects these records belong to (bank records, bullets, or "At X as Y: ..." facts)."""
    owners = {r.owner for r in profile.experience_bank if r.text in texts}
    owners |= {e.company for e in profile.experience if any(h in texts or any(h in t for t in texts)
                                                             for h in e.highlights)}
    for t in texts:
        m = re.match(r"^(?:At|Recognition at|Tools used at) (.+?)(?: as [^:]+)?:", t)
        if m:
            owners.add(m.group(1))
    return owners


def record_ids(texts: list[str], profile: Profile) -> list[dict]:
    """The records behind these texts by id: an experience-bank id ("AG24") where the text is a bank record."""
    bank = {r.text: r.id for r in profile.experience_bank}
    out = []
    for t in texts:
        plain = t.split(": ", 1)[1] if re.match(r"^(At|Project|Achievement|Recognition) ", t) and ": " in t else t
        if plain in bank:
            out.append({"id": bank[plain], "text": plain})
    return out


def claim_text(sentence: str) -> str:
    """The claim itself: "At Contoso, I also build X" -> "built X" (for the ownership scale)."""
    s = re.sub(r"^\s*(?:At|In|With|For|While at) [^,]{2,80},\s*", "", sentence.strip())
    s = re.sub(r"^(?:I|I've|I have|I've also|I also|I then|I recently)\s+(?:also\s+|then\s+)?", "", s)
    first = s.split(" ", 1)
    if first and first[0].lower() in _PAST:
        s = _PAST[first[0].lower()] + (" " + first[1] if len(first) > 1 else "")
    return s


def _record_text(t: str) -> str:
    return t.split(": ", 1)[1] if re.match(r"^(At|Project|Achievement|Recognition) ", t) and ": " in t else t


def check(sentence: str, support: list[str], profile: Profile, job: JobOut, everything: str | None = None,
          posting_words: bool = True, employer_check: bool = True, context: list[str] | None = None,
          tool_owners: bool | str = False) -> list[dict]:
    """Each claim in ``sentence`` that its evidence doesn't back: [{kind, detail}].

    ``support``: the records the sentence was written from (or, for a letter, the records closest to it). Tools
    and wording may come from anywhere in your profile (``everything``); numbers, employers, leadership,
    ownership, results, audiences and responsibilities must be in ``support``. ``context`` (your own summary line)
    may back numbers only (years of experience). ``tool_owners`` (letters and answers, which name employers): each
    tool must be one the named employer's records show, and one piece of work never joins tools from different
    employers ("I ran Selenium tests through Jenkins pipelines" when Selenium and Jenkins were at two jobs)."""
    from app import scorecard
    from app.vocabulary import find_terms
    everything = evidence_text(profile) if everything is None else everything
    evidence = " ".join(support)
    out: list[dict] = []
    named = {n for _, _, n in find_terms(sentence) if ats.is_named_tool(n)}
    for skill in job.required_skills + job.preferred_skills:
        for term in ats.skill_terms(skill):
            if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", sentence.lower()):
                named.add(term)
    for tool in sorted(named):
        if ats.is_named_tool(tool) and not ats.supports_skill(everything, tool, profile):
            out.append({"kind": "tool", "detail": f"{tool} is not in your profile"})
    nums = lambda t: set(_NUMBER.findall(t)) | {w.lower() for w in NUMBER_WORDS.findall(t)}
    extra = sorted(nums(sentence) - nums(evidence) - nums(job.title) - nums(" ".join(context or [])))
    if extra:
        out.append({"kind": "metric", "detail": f"number(s) {', '.join(extra)} not in the records it summarises"})
    out += measure_problems(sentence, support)
    if employer_check:
        owners = owners_of(support, profile)
        for company in sorted(employers_named(sentence, profile)):
            if company not in owners:
                out.append({"kind": "employer", "detail": f"names {company}, but this work is from "
                                                          f"{', '.join(sorted(owners)) or 'elsewhere'}"})
    if tool_owners and not any(x["kind"] == "employer" for x in out):
        out += tool_owner_problems(sentence, profile, join=tool_owners != "named")
    for m in LEADERSHIP.finditer(sentence):
        stem = m.group(1).lower()[:4]
        low = evidence.lower()
        if not re.search(rf"\b{re.escape(stem)}", low) and not (stem.startswith("lead") and re.search(r"\bled\b", low)) \
                and not (stem == "led" and re.search(r"\blead", low)):
            out.append({"kind": "leadership", "detail": f"\"{m.group(1)}\" is not in the records"})
            break
    claim = claim_text(sentence)
    verb = scorecard.first_verb(claim)
    if not any(x["kind"] == "leadership" for x in out) and scorecard.ownership(claim)[1] == "owner" and not any(
            scorecard.ownership(_record_text(x))[1] == "owner" or re.search(rf"\b{re.escape(verb)}\b", x.lower())
            for x in support):
        out.append({"kind": "ownership", "detail": f"\"{verb}\" claims more ownership than the records"})
    missing = outcome_kinds(sentence) - outcome_kinds(evidence)
    if "benefit" in missing and support and outcome_kinds(support[0]) - {"benefit"}:
        missing.discard("benefit")           # the record it's about reports a result: saying what it gave is fair
    if missing:
        word = next(m.group(1) for m in OUTCOMES.finditer(sentence) if outcome_kinds(m.group(1)) & missing)
        out.append({"kind": "outcome", "detail": f"\"{word}\" but no record it describes reports that result"})
    team = lambda t: _TEAM_NAMES.sub("support team", t)
    have_aud = {_aud(m.group(1)) for m in AUDIENCE.finditer(team(evidence))}
    for m in AUDIENCE.finditer(team(sentence)):
        if _aud(m.group(1)) not in have_aud:
            near = _TEAM_NAMES.search(evidence)
            out.append({"kind": "audience", "detail": f"\"{m.group(1)}\" but its records "
                        + (f"say \"{near.group(0)}\" (a team you worked with)" if near else "name no such audience")})
            break
    have_resp = {_resp(m.group(1)) for m in RESPONSIBILITY.finditer(evidence)}
    for m in RESPONSIBILITY.finditer(sentence):
        if _resp(m.group(1)) not in have_resp:
            out.append({"kind": "responsibility", "detail": f"\"{m.group(1)}\" is not a responsibility its records state"})
            break
    out += tool_context_problems(sentence)
    if posting_words:
        from app.tailoring import _GENERIC_POSTING_WORDS, _stem_word, _words
        have = {_stem_word(w) for w in _words(everything)}
        taken = sorted(w for w in (_words(sentence) & _words(job.description))
                       if w not in _GENERIC_POSTING_WORDS and _stem_word(w) not in have and len(w) > 3)
        if taken:
            out.append({"kind": "posting_duty", "detail": "posting wording your profile doesn't use: "
                                                          + ", ".join(taken)})
    return out


_OWNER_TEXT: dict[int, tuple[Profile, dict[str, str]]] = {}


def owner_texts(profile: Profile) -> dict[str, str]:
    """Each employer's and project's own evidence as one text: its bullets, verified bank records and tools."""
    hit = _OWNER_TEXT.get(id(profile))
    if hit and hit[0] is profile:
        return hit[1]
    out: dict[str, list[str]] = {}
    for e in profile.experience:
        out.setdefault(e.company, []).extend(list(e.highlights) + list(e.skills))
    for pr in profile.projects:
        out.setdefault(pr.name, []).extend(list(pr.highlights or []) + list(pr.skills or []) + [pr.description or ""])
    for r in profile.experience_bank:
        if r.status == "verified":
            out.setdefault(r.owner, []).append(r.text)
    texts = {k: " ".join(v) for k, v in out.items()}
    if len(_OWNER_TEXT) > 8:
        _OWNER_TEXT.clear()
    _OWNER_TEXT[id(profile)] = (profile, texts)
    return texts


# a plain list of what you know ("I bring experience with A, B and C"): tools from any job may be listed together
_SKILLS_LINE = re.compile(r"^(?:I\s+(?:also\s+)?(?:bring|offer|have (?:used|worked with)|'ve used|work with|am "
                          r"familiar with|know)|I've (?:also )?used|My (?:tools|skills))\b", re.IGNORECASE)


def tool_owner_problems(sentence: str, profile: Profile, join: bool = True) -> list[dict]:
    """Each tool told where your records show it: "At Contoso, I used Selenium" when Selenium is in your Fabrikam
    and Tailspin records only; "I ran Selenium tests through Jenkins pipelines" when the two were at different
    jobs (a sentence names one piece of work)."""
    from app.vocabulary import find_terms
    texts = owner_texts(profile)
    tools = list(dict.fromkeys(n for _, _, n in find_terms(sentence) if ats.is_named_tool(n)))
    where = {t: {o for o, txt in texts.items() if ats.supports_skill(txt, t)} for t in tools}
    where = {t: o for t, o in where.items() if o}             # a tool only in your skills list: no owner to check
    named = employers_named(sentence, profile) | ({p.name for p in profile.projects
                                                   if p.name.lower() in sentence.lower()})
    out = []
    if len(named) == 1:
        owner = next(iter(named))
        for t, owners in where.items():
            if owner not in owners:
                out.append({"kind": "employer", "detail": f"{t} is not in your {owner} records (it is from "
                                                          f"{', '.join(sorted(owners))})"})
        return out
    if not join or named or len(where) < 2 or _SKILLS_LINE.search(sentence):
        return out
    if not set.intersection(*where.values()):
        said = "; ".join(f"{t}: {', '.join(sorted(o))}" for t, o in where.items())
        out.append({"kind": "employer", "detail": f"joins tools from different jobs into one piece of work ({said})"})
    return out


def _aud(word: str) -> str:
    return re.sub(r"s$", "", word.lower().replace("-", " "))


def _resp(word: str) -> str:
    w = word.lower().replace("-", " ")
    return {"sign off": "signoff", "sign offs": "signoff", "signoffs": "signoff", "readiness decision": "readiness decisions",
            "roadmaps": "roadmap", "budgets": "budget", "strategies": "strategy", "strategic": "strategy"}.get(w, w)[:12]


# --- measurements keep their unit -------------------------------------------------------------------------------
_NOT_UNIT = {"to", "and", "of", "the", "by", "in", "for", "from", "with", "on", "at", "while", "across", "an", "a",
             "or", "per", "than", "approximately", "about", "average", "over", "under", "into", "as", "each"}


def measure_of(number: str, record: str) -> str | None:
    """The unit or measure a record gives a number: "2,048,000-document workload" -> "document", "65%" -> "%",
    "120.5 seconds" -> "second". None: the number stands alone in the record (a count said with its noun before)."""
    m = re.search(rf"(?<![\d.,]){re.escape(number)}(?![\d])(\s*%|\s*percent\b|(?:-|\s+)([a-z][a-z]+))", record)
    if not m:
        return None
    if "%" in m.group(1) or "percent" in m.group(1):
        return "%"
    unit = m.group(2)
    return None if unit in _NOT_UNIT else re.sub(r"(es|s)$", "", unit)


def measure_problems(sentence: str, support: list[str]) -> list[dict]:
    """A number its record gives with a unit must keep it: "while preserving the same 2,048,000." drops
    "-document workload"."""
    out = []
    for n in dict.fromkeys(_NUMBER.findall(sentence)):
        units = {u for r in support if (u := measure_of(n, r))}
        if not units:
            continue
        after = sentence[sentence.find(n) + len(n):][:40].lower()
        said = re.match(r"\s*(%|percent)", after) if "%" in units else None
        words = [re.sub(r"(es|s)$", "", w) for w in re.findall(r"[a-z]+", after)[:3]]
        if not said and not any(u in words for u in units if u != "%"):
            record = next(r for r in support if measure_of(n, r))
            phrase = re.search(rf"{re.escape(n)}[^,.;]*", record).group(0)
            out.append({"kind": "metric", "detail": f"{n} without its measure (the record says \"{phrase}\")"})
    return out


def restore_measure(sentence: str, support: list[str]) -> str | None:
    """Put the record's measure back: "... the same 2,048,000." -> "... the same 2,048,000-document workload."."""
    fixed = sentence
    for p in measure_problems(sentence, support):
        n = p["detail"].split(" ", 1)[0]
        phrase = p["detail"].split('says "', 1)[1].rstrip('")')
        if not re.search(rf"{re.escape(n)}\s*[.,;!?]|{re.escape(n)}\s*$", fixed):
            return None                       # words follow the number: putting the unit back would garble it
        fixed = re.sub(rf"{re.escape(n)}(?=\s*[.,;!?]|\s*$)", phrase, fixed, count=1)
    return fixed if fixed != sentence else None


# --- a tool is told with the kind of work it was used for --------------------------------------------------------
TOOL_KINDS = {
    "CI/CD": ({"jenkins", "github actions", "gitlab ci", "gitlab ci/cd", "circleci", "azure devops", "azure pipelines",
               "teamcity", "bamboo", "travis ci", "argo cd"},
              r"ci/cd|ci|cd|pipelines?|builds?|deployments?|continuous integration|releases?"),
    "data": ({"sql", "mysql", "postgresql", "oracle", "pl/sql", "t-sql", "snowflake", "databricks", "pandas", "pyspark",
              "sql server", "sqlite"},
             r"data|databases?|quer(?:y|ies)|validation|validat\w+|etl|reconcil\w+|tables?|schemas?|mappings?|records?"),
}


def _kind_of(tool: str) -> str | None:
    return next((k for k, (tools, _) in TOOL_KINDS.items() if tool.lower() in tools), None)


def tool_lists(sentence: str) -> list[list[tuple[int, int, str]]]:
    """Runs of named tools joined only by commas, "and", "or" or "/": "using Jenkins, GitHub Actions, and SQL"."""
    from app.vocabulary import find_terms
    hits = [h for h in find_terms(sentence) if ats.is_named_tool(h[2])]
    runs: list[list[tuple[int, int, str]]] = []
    for h in hits:
        if runs and re.fullmatch(r"(?:\s*,\s*|\s+|\s*/\s*)(?:(?:and|or)\s+)?", sentence[runs[-1][-1][1]:h[0]]):
            runs[-1].append(h)
        else:
            runs.append([h])
    return runs


def _segments(text: str) -> list[str]:
    """A sentence's clauses: split at commas, "and", "while", ";"."""
    return [x for x in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=I\b|[a-z]+(?:ed|e|s)?\s)|;\s*|\s+while\s+", text)
            if x.strip()] or [""]


def _work_kinds(segment: str) -> set[str]:
    seg = re.sub(r"\bdata pipelines?\b", "data", segment.lower())      # "validate data pipelines": data work
    return {k for k, (_, ctx) in TOOL_KINDS.items() if re.search(rf"\b(?:{ctx})\b", seg)}


def tool_context_problems(sentence: str) -> list[dict]:
    """ "I maintain CI/CD pipelines using Jenkins, GitHub Actions, and SQL": SQL is told as CI/CD work. A list's
    tools must fit the work named just before it (or be a plain list of skills: "experience with ...")."""
    out = []
    for run in tool_lists(sentence):
        if len(run) < 2:
            continue
        named = _work_kinds(_segments(sentence[:run[0][0]])[-1] if sentence[:run[0][0]].strip() else "")
        kinds = {_kind_of(h[2]) for h in run} - {None}
        if len(named) != 1 or not (kinds & named):
            continue                          # no kind of work named, or both: nothing to mismatch
        if re.match(r"\s+(?:jobs?|checks?|scripts?|quer(?:y|ies)|tests?|suites?|steps?)\b", sentence[run[-1][1]:]):
            continue                          # "using SQL and Jenkins jobs": the tools name the parts used
        for h in run:
            k = _kind_of(h[2])
            if k and k not in named:
                out.append({"kind": "tool_context", "detail": f"{h[2]} is listed with {next(iter(named))} work; "
                                                             f"describe it with {k} work"})
    return out


def move_misplaced_tools(sentence: str) -> str | None:
    """Take misplaced tools out of their list and name them with the work they were used for, when the sentence
    names such work elsewhere: "I maintain CI/CD pipelines, automate testing, and validate data pipelines using
    Jenkins, GitHub Actions, and SQL." -> "I maintain CI/CD pipelines with Jenkins and GitHub Actions, automate
    testing, and validate data pipelines using SQL." Without such work, the tool is only taken out of the list.
    None: nothing to move."""
    problems = tool_context_problems(sentence)
    if not problems:
        return None
    moving: dict[str, list[str]] = {}
    for p in problems:
        moving.setdefault(p["detail"].rsplit("with ", 1)[1].removesuffix(" work"), []).append(
            p["detail"].split(" is listed", 1)[0])
    s = sentence
    for kind, tools in moving.items():
        run = next(r for r in tool_lists(s) if any(h[2] in tools for h in r))
        kept = [h[2] for h in run if h[2] not in tools]
        if not kept:
            return None
        s = s[:run[0][0]] + _join(kept) + s[run[-1][1]:]
        # the first other clause that names this kind of work gets the tools ("CI/CD pipelines with Jenkins")
        ctx = TOOL_KINDS[kind][1]
        pos = 0
        for seg in _segments(s):
            start = s.find(seg, pos)
            pos = start + len(seg)
            if kind in _work_kinds(seg) and not any(h[2] in kept for r in tool_lists(seg) for h in r):
                m = list(re.finditer(rf"\b(?:{ctx})\b", re.sub(r"\bdata pipelines?\b", "data xxxxxxxxx", seg.lower())))
                if m:
                    end = start + len(seg.rstrip(" .!?"))
                    s = s[:end] + f" with {_join(tools)}" + s[end:]
                    break
    return s if s != sentence else None


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# --- a grounded rewrite without the model ----------------------------------------------------------------------
# a clause that adds a result, a benefit or a purpose to the activity: removing it leaves what you did
_ADDED_CLAUSE = re.compile(
    r"(?:,\s*|\s+)(?:which|making|so that|helping|allowing|enabling|resulting|giving|to (?:help|make|ensure|"
    r"catch|reduce|improve|prevent|speed|enable)|and (?:thereby|so)|thereby|in order to)\b.*$", re.IGNORECASE)


def without_added_clause(sentence: str) -> str | None:
    """ "I ran release suites on Kubernetes through Jenkins pipelines, which made it easier to catch issues before
    deployments." -> "I ran release suites on Kubernetes through Jenkins pipelines." (None: nothing to remove,
    or too little would be left)."""
    m = _ADDED_CLAUSE.search(sentence.strip())
    if not m:
        return None
    head = sentence[:m.start()].rstrip(" ,;")
    if len(head.split()) < 5:
        return None
    return head + "."
