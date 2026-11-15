"""Your contact details never go into an AI prompt; the AI only needs your experience. FICTIONAL data."""

import json
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SECRETS = {"email": "jordan.private@example.com", "phone": "+1 (555) 010-7788", "address": "12 Fictional Lane, Toronto"}


@pytest.fixture
def private_profile(profile_data):
    data = json.loads(json.dumps(profile_data))
    data["contact"].update({"email": SECRETS["email"], "phone": SECRETS["phone"], "address": SECRETS["address"]})
    return data


def _all_prompts(fake_llm) -> str:
    return "\n".join(c["system"] + "\n" + c["user"] for c in fake_llm.calls)


def test_no_contact_details_in_any_prompt(client, fake_llm, private_profile, job_data):
    client.put("/profile", json=private_profile)
    job_id = client.post("/jobs", json={**job_data, "allow_duplicate": True}).json()["id"]
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.answer = {"text": "I build pytest suites.", "fact_ids_used": [], "bullets": [], "sentences": [],
                       "answers": [], "required_skills": ["Python"], "preferred_skills": []}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"})
    client.post(f"/applications/{app_id}/drafts", json={"kind": "screening_answer", "question": "Why us?"})
    client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"})
    client.post(f"/jobs/{job_id}/match/ai")
    client.post("/ai/extract-skills", json={"description": job_data["description"]})
    prompts = _all_prompts(fake_llm)
    assert len(fake_llm.calls) >= 5
    for name, value in SECRETS.items():
        assert value not in prompts, f"{name} was sent to the AI"


def test_resume_still_shows_your_contact_details(client, fake_llm, private_profile, job_data):
    """Privacy is about what the AI sees; your own resume obviously still has your email and phone."""
    client.put("/profile", json=private_profile)
    job_id = client.post("/jobs", json={**job_data, "allow_duplicate": True}).json()["id"]
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.answer = {"bullets": [], "sentences": []}
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    page = client.get(f"/documents/{doc['id']}/print").text
    assert SECRETS["email"] in page and SECRETS["address"] not in page       # home address is never printed
