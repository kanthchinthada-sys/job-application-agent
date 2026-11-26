"""Compare providers on the same fictional portfolio and posting, without saving keys or documents."""

from datetime import UTC, datetime
import json
from concurrent.futures import ThreadPoolExecutor

from app import drafting, evaluation, metrics, tailoring
from app.llm import LLMOutputError, LLMUnavailableError
from app.schemas import BankRecord, JobOut, Profile


def samples() -> list[dict]:
    return json.loads(evaluation.CASES.read_text())["jobs"]


def compare(clients, case, pricing) -> list[dict]:
    profile = Profile.model_validate_json(evaluation.PROFILE.read_text())
    if not profile.experience_bank:
        corpus = [(item.company, "experience", line) for item in profile.experience for line in item.highlights]
        corpus += [(item.name, "project", line) for item in profile.projects for line in item.highlights]
        profile.experience_bank = [
            BankRecord(id=f"EV{i:02d}", owner=owner, owner_kind=owner_kind, text=text, status="verified")
            for i, (owner, owner_kind, text) in enumerate(corpus, 1)
        ]
    job = JobOut(id=0, title=case["title"], company=case["company"], location=case["location"],
                 description=case["description"], required_skills=case["gold_required"],
                 preferred_skills=case["gold_preferred"], created_at=datetime.now(UTC))

    def run(entry):
        name, client = entry
        row = {"provider": name, "model": client.model}
        with metrics.collect(new=True) as measured:
            try:
                resume_text, resume_review = tailoring.tailor(client, profile, job)
                letter, letter_review = drafting.draft(client, profile, job, "cover_letter")
                resume = tailoring.TailoredResume.model_validate_json(resume_text)
                row.update(summary=resume.summary, skills=resume.skills, letter=letter,
                           resume_warnings=resume_review.get("warnings", []),
                           letter_warnings=letter_review.warnings, letter_removed=len(letter_review.removed),
                           humanizer_score=letter_review.humanizer.score if letter_review.humanizer else None)
            except (LLMOutputError, LLMUnavailableError) as exc:
                row["error"] = str(exc)
        stats = measured.report()
        row.update(seconds=stats["total_seconds"], input_tokens=stats["input_tokens"],
                   output_tokens=stats["output_tokens"], calls=stats["model_calls"], estimated_usd=None)
        rates = pricing.get(name)
        if rates and stats["input_tokens"] is not None and stats["output_tokens"] is not None:
            row["estimated_usd"] = round(
                (stats["input_tokens"] * rates.input + stats["output_tokens"] * rates.output) / 1_000_000, 6)
        return row

    with ThreadPoolExecutor(max_workers=len(clients)) as workers:
        return list(workers.map(run, clients.items()))
