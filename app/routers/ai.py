from typing import Annotated, Literal

from fastapi import APIRouter, Request
from app.dependencies import db_path, llm as account_llm
from pydantic import StringConstraints

from app.extraction import ExtractionResult, extract_skills
from app.schemas import StrictModel

router = APIRouter(prefix="/ai", tags=["ai"])


class ExtractRequest(StrictModel):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=20_000)]


@router.get("/status")
def ai_status(request: Request):
    """Is this account's AI reachable: Claude or OpenAI with its own key, or the local Ollama model?"""
    return account_llm(request).status()


class AISettingsIn(StrictModel):
    provider: Literal["claude", "openai", "ollama"]
    # a new key; leave out to keep the saved one (when the provider stays the same)
    api_key: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=20, max_length=400)] = None
    model: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)] = None


@router.get("/settings")
def get_ai_settings(request: Request):
    """This account's AI provider. The API key itself is never returned (only its last 4 characters)."""
    from app import ai_settings
    return ai_settings.public(ai_settings.load(db_path(request)))


@router.put("/settings", responses={400: {"description": "Key not accepted by the provider"}})
def put_ai_settings(body: AISettingsIn, request: Request):
    """Use Claude or OpenAI with your own API key (checked with the provider first, at no cost), or the local
    model. Applies at once, also to resumes and cover letters already being prepared."""
    from fastapi import HTTPException
    from app import ai_settings
    from app.llm import ClaudeClient, OpenAIClient
    path = db_path(request)
    if body.provider == "ollama":
        ai_settings.save(path, "ollama", None, None)
        return ai_settings.public(ai_settings.load(path))
    saved = ai_settings.load(path) or {}
    key = body.api_key or (saved.get("api_key") if saved.get("provider") == body.provider else None)
    if not key:
        raise HTTPException(400, f"Paste your {'Claude' if body.provider == 'claude' else 'OpenAI'} API key.")
    client = (ClaudeClient if body.provider == "claude" else OpenAIClient)(key, body.model)
    check = client.status()
    if not check.get("model_available"):
        why = check.get("error") or "could not reach the provider"
        raise HTTPException(400, f"Not saved: {why} ({check.get('model')}).")
    ai_settings.save(path, body.provider, key, client.model)
    return ai_settings.public(ai_settings.load(path))


@router.post(
    "/extract-skills",
    response_model=ExtractionResult,
    responses={502: {"description": "Model gave an unusable answer"}, 503: {"description": "Ollama not available"}},
)
def extract(body: ExtractRequest, request: Request):
    """Paste a job description; get suggested required/preferred skills.

    Nothing is saved. Review the suggestions, then create the job with POST /jobs.
    Takes roughly 10-60 seconds on a laptop.
    """
    return extract_skills(account_llm(request), body.description)


@router.post("/evaluate")
def evaluate(request: Request, letters: bool = True):
    """Run the AI quality evaluation on the FICTIONAL cases in examples/eval_cases.json.

    Slow (several minutes with a local model). Uses no personal data and saves nothing.
    """
    from app.evaluation import run
    return run(account_llm(request), letters=letters)
