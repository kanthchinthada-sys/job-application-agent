"""AI second look at the skills the rule-based match could not find ("ai_related_v1").

The model may only point at your own profile facts, and must quote the exact words that show the skill.
Code then checks every quote really is in that fact; anything else is thrown away. Results are labelled as
AI suggestions for you to check: they never change the rule-based score.
"""

import re

from pydantic import BaseModel, Field, ValidationError

from app.drafting import build_facts
from app.llm import LLMClient, LLMOutputError
from app.schemas import Profile

METHOD = "ai_related_v1"

SYSTEM = """You compare a job's requirements with one candidate's real experience.
For each requirement, decide whether ONE of the numbered facts shows the candidate has that skill or very
closely related experience. Be strict: only answer when a reasonable hiring manager would agree.
- fact_id: the id of that fact, or "" if no fact shows it.
- quote: the exact words copied from that fact (3-25 words) that show it. Copy them exactly.
- reason: one short sentence on why they show it.
Never invent experience. Saying "" is the right answer when the facts do not show it."""


class _Answer(BaseModel):
    requirement: str
    fact_id: str = ""
    quote: str = ""
    reason: str = ""


class _Answers(BaseModel):
    answers: list[_Answer] = Field(default_factory=list)


class AISuggestion(BaseModel):
    requirement: str
    fact_id: str
    fact: str          # the whole profile fact
    quote: str         # the words in it that show the skill (checked to be really there)
    reason: str


class AIMatchReport(BaseModel):
    method: str = METHOD
    method_label: str = ("AI suggestions: the local AI found related experience in your profile. "
                         "Each quote was checked to be in your profile; whether it really fits is for you to judge.")
    model: str
    suggestions: list[AISuggestion]
    still_missing: list[str]
    rejected: int      # AI answers thrown away (made-up quote or unknown fact)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w/+#.-]+", " ", text.lower()).split())


def second_look(llm: LLMClient, profile: Profile, requirements: list[str]) -> AIMatchReport:
    if not requirements:
        return AIMatchReport(model=llm.model, suggestions=[], still_missing=[], rejected=0)
    from app.bank import bank_profile
    facts = {f.id: f.text for f in build_facts(bank_profile(profile))}
    user = ("REQUIREMENTS\n" + "\n".join(f"- {r}" for r in requirements) +
            "\n\nCANDIDATE FACTS\n" + "\n".join(f"{fid}: {text}" for fid, text in facts.items()) +
            "\n\nAnswer once per requirement, in the same order.")
    raw = llm.generate_json(SYSTEM, user, _Answers.model_json_schema())
    try:
        answers = _Answers.model_validate(raw).answers
    except ValidationError as exc:
        raise LLMOutputError("The model's answer did not match the expected format.") from exc

    wanted = {r.lower(): r for r in requirements}
    suggestions: list[AISuggestion] = []
    rejected = 0
    for a in answers:
        req = wanted.get(a.requirement.strip().lower())
        if not req or not a.fact_id:
            continue
        fact = facts.get(a.fact_id.strip())
        quote_ok = fact is not None and len(a.quote.split()) >= 3 and _norm(a.quote) in _norm(fact)
        if not quote_ok:
            rejected += 1
            continue
        if any(s.requirement == req for s in suggestions):
            continue
        suggestions.append(AISuggestion(requirement=req, fact_id=a.fact_id.strip(), fact=fact,
                                        quote=a.quote.strip(), reason=a.reason.strip()))
    found = {s.requirement for s in suggestions}
    return AIMatchReport(model=llm.model, suggestions=suggestions,
                         still_missing=[r for r in requirements if r not in found], rejected=rejected)
