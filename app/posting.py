"""Understand a job posting's structure and its ATS keywords ("posting_reader_v2").

Postings name their sections in endless ways ("What you'll bring", "The essentials", "Not a must but a plus",
"You should apply if", "Minimum requirements for the role", "What makes you a great fit", ...). Instead of a list
of phrases, each heading is classified by the words in it:

* required    qualifications, requirements, must, bring, need, looking for, about you, your experience, ...
* preferred   nice to have, bonus, plus, preferred, desirable, ideally, not a must, also has, ...
* duties      what you'll do, responsibilities, your impact, in this role, your assignment, ...
* ignore      benefits, compensation, about us / the company, equal opportunity, interview process, ...

A sub-heading that says none of these ("Solution Delivery") belongs to the section it sits in, and lines before
any heading are the introduction. A requirement line that says "is a plus" or "ideally" counts as preferred.

Keywords are the short terms an ATS scans for (tools, technologies, practices, a few people skills), found with
the vocabulary in ``app/vocabulary.py``. "Either Go, Java or Node.js" becomes ONE requirement with alternatives
("Go/Java/Node.js"), so knowing any of them meets it.
"""

import re

from app import vocabulary

_IGNORE = re.compile(
    r"\b(benefits?|perks?|what we offer|our offer|we offer|rewards?|compensation|salary|pay( range| transparency)?|"
    r"equal (opportunity|employment)|accommodations?|privacy|diversity|belonging|inclusion|interview|hiring process|"
    r"how to apply|apply now|before you hit apply|why (join|you'?ll|work|people)|about (us|the company|our company)|"
    r"who we are|our (story|values|culture|commitment|mission|team and company)|life at|working at|get to know|"
    r"where|locations?|fraudulent|e-verify|right to work|what you'?ll get|what you get|total rewards|"
    r"time off|vacation|health|insurance|401k|rrsp|equity|stock|parental|remote work|flexible|wellness|#li)\b",
    re.IGNORECASE)
_PREFERRED = re.compile(
    r"\b(nice[\s-]*to[\s-]*haves?|bonus|plus(es)?|preferred|desired|desirable|ideally|added advantage|"
    r"not (a )?must|even better|stand out|great to have|good to have|extra credit|also has|optional|"
    r"would be (great|nice|helpful)|brownie points|advantage)\b", re.IGNORECASE)
_REQUIRED = re.compile(
    r"\b(qualifications?|requirements?|required|must[\s-]*haves?|must|bring|need|needed|looking for|you have|"
    r"you'?ve|about you|who you are|you are|your (experience|background|skills|profile|qualifications|expertise)|"
    r"essentials?|skills|experience|expertise|knowledge|great fit|apply if|get in touch if|success(ful)?|"
    r"ideal candidate|what it takes|competenc(y|ies)|education|is this you|you might|you'?ll thrive)\b",
    re.IGNORECASE)
_DUTIES = re.compile(
    r"\b(do|doing|responsibilit(y|ies)|assignment|impact|the role|your role|in this role|you will|you'?ll|own|"
    r"day[\s-]to[\s-]day|deliver|accountabilit(y|ies)|opportunity|mission|projects|duties|the job|overview|"
    r"summary|what you will)\b", re.IGNORECASE)
_INLINE_PREFERRED = re.compile(r"\b(nice to have|is a plus|a plus|are a plus|bonus|preferred|desirable|ideally|"
                               r"added advantage|an advantage|not required)\b", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-•*·▪◦●○■□➢➤►–—]|\d+[.)])\s*")
# Lines about where / how you work or how hiring goes, not about skills: never a requirement or a keyword.
LOGISTICS = re.compile(
    r"\b(hybrid|in[\s-]person|on[\s-]?site|in[\s-]office|(\d+|two|three|four) days (a|per) week|days per week|"
    r"office locations?|list of offices|commut\w*|background check|drug test|"
    r"application experience|reasonable adjustments?|recruitment process|"
    r"hiring process|interview process|we value|our culture|equal opportunity|accommodations?)\b",
    re.IGNORECASE)


def _clean(line: str) -> str:
    return " ".join(line.replace("’", "'").replace("‘", "'").replace(" ", " ").split())


def classify_heading(text: str) -> str | None:
    """'required' | 'preferred' | 'duties' | 'ignore' | None (says none of these)."""
    h = _clean(text).lower().rstrip(":").strip()
    if re.match(r"^(about|who) (you|the role|this role|the position)\b", h):
        return "required" if " you" in f" {h}" and "role" not in h else "duties"
    if re.search(r"\b(what you'?ll get|what you get|why you'?ll|what's in it for you|your benefits|perks for you|"
                 r"you'?ll (enjoy|receive|get)|we offer you)\b", h):
        return "ignore"
    if _PREFERRED.search(h):
        return "preferred"
    if _IGNORE.search(h) and not re.search(r"\b(you|your)\b", h):
        return "ignore"
    if _REQUIRED.search(h):
        return "required"
    if _DUTIES.search(h):
        return "duties"
    if _IGNORE.search(h):
        return "ignore"
    return None


def _is_heading(line: str, next_line: str | None) -> bool:
    if not line or _BULLET.match(line) or next_line is None:
        return False
    words = line.rstrip(":").split()
    if len(words) > 12 or len(line) > 90:
        return False
    if line.endswith(":"):
        return True
    if line.endswith((".", ",", ";")):
        return False
    return len(words) <= 8


def sections(description: str) -> list[tuple[str, str, list[str]]]:
    """[(kind, heading, lines)] in order. kind: intro, required, preferred, duties, ignore."""
    raw = [_clean(x) for x in description.splitlines()]
    lines = [x for x in raw if x]
    out: list[tuple[str, str, list[str]]] = [("intro", "", [])]
    kind = "intro"
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if _is_heading(line, nxt):
            k = classify_heading(line)
            # inside a section, a short line without ":" is a new heading only if it names another kind of
            # section ("Nice to have" inside the requirements); otherwise it's a requirement written plainly
            in_section = kind in ("required", "preferred", "duties")
            # ...but a bare one-to-three word line ("Required", "Must have") is always a sub-heading
            if (k is not None and not line.endswith(":") and in_section and len(line.split()) > 3
                    and (k == kind or len(line.split()) > 6)):
                out[-1][2].append(_BULLET.sub("", line))
                continue
            if k is not None:
                kind = k
                out.append((kind, line.rstrip(":"), []))
                continue
            if kind == "intro" and len(line.split()) <= 6:
                out.append((kind, line, []))       # a sub-heading in the introduction
                continue
        # "Responsibilities: build the X..." / "Skills and Experience needed for success in this role: - ..."
        m = re.match(r"^([^:]{3,80}):\s+(\S.*)$", line)
        if m and not _BULLET.match(line) and classify_heading(m.group(1)) in ("required", "preferred", "duties"):
            kind = classify_heading(m.group(1))
            out.append((kind, m.group(1), [m.group(2)]))
            continue
        text = _BULLET.sub("", line)
        if LOGISTICS.search(text):
            # "Hybrid 2 days per week...", "We value in-person interactions...": how you work, not what you know
            out.append(("ignore", "(where and how you work)", [text]))
            out.append((kind, "", []))
            continue
        if kind == "required" and _INLINE_PREFERRED.search(text):
            out.append(("preferred", "(said to be optional)", [text]))
            out.append(("required", "", []))
            continue
        out[-1][2].append(text)
    return [s for s in out if s[2] or (s[0] != "intro" and s[1])]


def requirement_lines(description: str) -> tuple[list[str], list[str], list[str]]:
    """(required lines, preferred lines, duty lines)."""
    req, pref, duties = [], [], []
    for kind, _, lines in sections(description):
        {"required": req, "preferred": pref, "duties": duties}.get(kind, []).extend(lines)
    return req, pref, duties


def _with_alternatives(line: str, spans: list[tuple[int, int, str]]) -> list[str]:
    """Terms of one line; "either Go, Java, Kotlin or Node.js" and "TypeScript/JavaScript" become one item."""
    low = line.lower()
    units: list[list] = []
    for st, en, term in spans:
        if units and re.fullmatch(r"\s*/\s*", low[units[-1][1]:st]):
            units[-1][1], units[-1][2] = en, units[-1][2] + [term]
        else:
            units.append([st, en, [term]])
    out, i = [], 0
    while i < len(units):
        chain, j, has_or = [units[i]], i, False
        while j + 1 < len(units):
            gap = low[units[j][1]:units[j + 1][0]]
            # items the vocabulary doesn't know may sit in the list too: "ADF, Synapse, Fabric, Power BI, or ..."
            if not re.fullmatch(r"(?:\s*,\s*[a-z][\w&+.\- ]{0,24}?)*\s*(,|,?\s*or|/|,?\s*and/or)\s*", gap) \
                    or re.search(r"\band\b(?!/or)", gap):
                break
            has_or = has_or or "or" in gap
            chain.append(units[j + 1])
            j += 1
        if has_or and len(chain) > 1:
            out.append("/".join(dict.fromkeys(t for u in chain for t in u[2])))
        else:
            out += ["/".join(dict.fromkeys(u[2])) for u in chain]
        i = j + 1
    return out


def terms_in_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        out += _with_alternatives(line, vocabulary.find_terms(line))
    return list(dict.fromkeys(out))


def keywords(description: str, max_required: int = 30, max_preferred: int = 15) -> tuple[list[str], list[str]]:
    """(required, preferred) ATS keywords: short terms, in the posting's order of importance."""
    secs = sections(description)
    kinds = {k for k, _, _ in secs}
    req_lines = [l for k, _, ls in secs if k == "required" for l in ls]
    pref_lines = [l for k, _, ls in secs if k == "preferred" for l in ls]
    duty_lines = [l for k, _, ls in secs if k == "duties" for l in ls]
    if "required" not in kinds:
        # no requirements section: the whole posting except the parts about the company and benefits
        req_lines = [l for k, _, ls in secs if k in ("intro", "duties") for l in ls]
        duty_lines = []
    required = terms_in_lines(req_lines)
    # a requirement "X/Y/Z" (any of them) already covers a duty that names one of them ("... Power BI ...")
    in_groups = {p.lower() for t in required if "/" in t for p in t.split("/")}
    for t in terms_in_lines(duty_lines):
        if t not in required and t.lower() not in in_groups:
            required.append(t)
    preferred = [t for t in terms_in_lines(pref_lines) if t not in required]
    # a term that is ALSO named on its own elsewhere doesn't need repeating inside an alternatives group
    singles = {t for t in required if "/" not in t}
    required = [t for t in required if "/" not in t or not set(t.split("/")) <= singles]
    return required[:max_required], preferred[:max_preferred]


def focus_text(description: str, limit: int = 3500) -> str:
    """The parts of a posting an AI needs to write for it: requirements, nice-to-haves and duties (in that
    order), without the company history, benefits and legal text. Shorter prompts are faster and are never
    cut off by the model's context window. Falls back to the start of the posting."""
    parts = []
    for kind, label in (("required", "REQUIREMENTS"), ("preferred", "NICE TO HAVE"), ("duties", "THE JOB")):
        lines = [l for k, _, ls in sections(description) if k == kind for l in ls]
        if lines:
            parts.append(label + ":\n" + "\n".join(f"- {l}" for l in lines))
    text = "\n\n".join(parts) if parts else description
    if len(text) <= limit:
        return text
    cut = text[:limit]                       # never in the middle of a word (the model would copy "integr")
    return cut[:max(cut.rfind(" "), cut.rfind("\n"))].rstrip(" ,;") + " ..."
