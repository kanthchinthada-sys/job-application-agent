"""AI skill extraction from a pasted job description ("llm_extraction_v1").

The model proposes skills; this code then checks every skill against the
original text. Anything the text does not actually contain is moved to
``needs_review`` instead of being trusted. You review the result before
saving the job, so the AI never writes data on its own.
"""

import re

from pydantic import BaseModel, Field, ValidationError

from app.llm import LLMClient, LLMOutputError
from app.matching import ALIASES, normalize

METHOD = "llm_extraction_v1"

SYSTEM_PROMPT = """You extract skills from job descriptions.
Rules:
- Only list skills, tools, technologies and competencies that are explicitly written in the text.
- Never add skills that are not in the text, even if they are usually related.
- Use the wording from the text. Keep each item short (1-4 words), e.g. "Python", "REST APIs", "Customer communication".
- required_skills: must-haves, requirements, qualifications.
- preferred_skills: nice-to-have, preferred, bonus, plus.
- If unsure whether a skill is required or preferred, put it in required_skills.
- List each skill once."""


class _ModelAnswer(BaseModel):
    """The JSON shape we force the model to return."""

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    method: str
    model: str
    required_skills: list[str]   # found in the text: safe to use
    preferred_skills: list[str]  # found in the text: safe to use
    needs_review: list[str]      # model suggested, but not found in the text
    note: str


def _clean(items: list[str]) -> list[str]:
    seen, result = set(), []
    for item in items:
        item = " ".join(str(item).split()).strip(" .,;:-")
        if item and len(item) <= 80 and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def found_in_text(skill: str, text: str) -> bool:
    """True if the skill (or an alias of it) appears as whole words in the text."""
    lowered = " ".join(text.lower().split())
    norm = normalize(skill)
    forms = {skill.lower(), norm} | {a for a, target in ALIASES.items() if target == norm}
    return any(re.search(rf"(?<!\w){re.escape(f)}(?!\w)", lowered) for f in forms)


def extract_skills(llm: LLMClient, description: str) -> ExtractionResult:
    raw = llm.generate_json(SYSTEM_PROMPT, f"Job description:\n\n{description}", _ModelAnswer.model_json_schema())
    try:
        answer = _ModelAnswer.model_validate(raw)
    except ValidationError as exc:
        raise LLMOutputError("The model's answer did not match the expected format.") from exc

    required, preferred, needs_review = [], [], []
    for skill in _clean(answer.required_skills):
        (required if found_in_text(skill, description) else needs_review).append(skill)
    for skill in _clean(answer.preferred_skills):
        if skill.lower() in {s.lower() for s in required}:
            continue
        (preferred if found_in_text(skill, description) else needs_review).append(skill)

    return ExtractionResult(
        method=METHOD,
        model=llm.model,
        required_skills=required,
        preferred_skills=preferred,
        needs_review=needs_review,
        note="AI suggestions. Review and edit them, then save the job with POST /jobs. "
        "'needs_review' items were not found word-for-word in the description.",
    )
