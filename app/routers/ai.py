from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import StringConstraints

from app.extraction import ExtractionResult, extract_skills
from app.schemas import StrictModel

router = APIRouter(prefix="/ai", tags=["ai"])


class ExtractRequest(StrictModel):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=20_000)]


@router.get("/status")
def ai_status(request: Request):
    """Is Ollama running, and is the model downloaded?"""
    return request.app.state.llm.status()


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
    return extract_skills(request.app.state.llm, body.description)


@router.post("/evaluate")
def evaluate(request: Request, letters: bool = True):
    """Run the AI quality evaluation on the FICTIONAL cases in examples/eval_cases.json.

    Slow (several minutes with a local model). Uses no personal data and saves nothing.
    """
    from app.evaluation import run
    return run(request.app.state.llm, letters=letters)
