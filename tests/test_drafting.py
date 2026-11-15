"""Drafting: honesty checks, versioning, and errors. Uses the fake model."""

import pytest

from app.drafting import build_facts
from app.schemas import Profile


@pytest.fixture
def app_id(client, profile_data, make_job):
    client.put("/profile", json=profile_data)
    job = make_job()
    return client.post("/applications", json={"job_id": job["id"]}).json()["id"]


def draft(client, app_id, **body):
    return client.post(f"/applications/{app_id}/drafts", json=body or {"kind": "cover_letter"})


def test_cover_letter_saved_with_cited_facts(client, fake_llm, app_id):
    fake_llm.answer = {"text": "Dear Hiring Team, I build pytest suites. Sincerely, Jordan",
                       "fact_ids_used": ["F5"], "questions_for_candidate": []}
    response = draft(client, app_id)
    assert response.status_code == 201
    doc = response.json()
    assert doc["kind"] == "cover_letter" and doc["version"] == 1 and doc["source"] == "ai"
    assert doc["model"] == "fake-model"
    assert doc["review"]["facts_used"][0]["id"] == "F5"
    assert doc["review"]["warnings"] == []


def test_prompt_contains_only_profile_facts_and_job(client, fake_llm, app_id, job_data):
    fake_llm.answer = {"text": "x", "fact_ids_used": ["F1"]}
    draft(client, app_id)
    call = fake_llm.calls[-1]
    assert "Never invent" in call["system"]
    assert "F1: Summary:" in call["user"]
    assert job_data["title"] in call["user"]


def test_invented_number_is_flagged(client, fake_llm, app_id):
    fake_llm.answer = {"text": "I cut test time by 73% across 12 teams.", "fact_ids_used": ["F1"]}
    warnings = draft(client, app_id).json()["review"]["warnings"]
    assert any("73" in w and "12" in w for w in warnings)


def test_numbers_from_profile_are_not_flagged(client, fake_llm, app_id):
    # "three internal teams" is text, but 2021 is a real start date in the fictional profile
    fake_llm.answer = {"text": "Since 2021 I have built pytest suites.", "fact_ids_used": ["F4"]}
    assert draft(client, app_id).json()["review"]["warnings"] == []


def test_claiming_a_missing_skill_is_flagged(client, fake_llm, app_id):
    # AWS is required by the fictional job but absent from the fictional profile
    fake_llm.answer = {"text": "I deploy services on AWS every day.", "fact_ids_used": ["F1"]}
    warnings = draft(client, app_id).json()["review"]["warnings"]
    assert any("AWS" in w for w in warnings)


def test_unknown_or_missing_citations_are_flagged(client, fake_llm, app_id):
    fake_llm.answer = {"text": "Some text.", "fact_ids_used": ["F999"]}
    warnings = draft(client, app_id).json()["review"]["warnings"]
    assert any("F999" in w for w in warnings)
    assert any("did not say which" in w for w in warnings)


def test_screening_answer_requires_question(client, app_id):
    assert draft(client, app_id, kind="screening_answer").status_code == 422
    assert draft(client, app_id, kind="cover_letter", question="Why us?").status_code == 422


def test_unanswerable_question_returns_questions_for_you(client, fake_llm, app_id):
    fake_llm.answer = {"text": "", "fact_ids_used": [], "questions_for_candidate": ["What is your notice period?"]}
    doc = draft(client, app_id, kind="screening_answer", question="What is your notice period?").json()
    assert doc["content"] == ""
    assert doc["review"]["questions_for_you"] == ["What is your notice period?"]
    assert any("could not answer" in w for w in doc["review"]["warnings"])


def test_versions_are_kept_and_edits_create_new_version(client, fake_llm, app_id):
    fake_llm.answer = {"text": "First draft.", "fact_ids_used": ["F1"]}
    first = draft(client, app_id).json()
    fake_llm.answer = {"text": "Second draft.", "fact_ids_used": ["F1"]}
    second = draft(client, app_id).json()
    edited = client.post(f"/documents/{second['id']}/versions", json={"content": "My edited letter."})
    assert edited.status_code == 201
    assert (first["version"], second["version"], edited.json()["version"]) == (1, 2, 3)
    assert edited.json()["source"] == "user_edit"

    docs = client.get(f"/applications/{app_id}/documents").json()
    assert docs[0]["content"] == "My edited letter."
    assert "Second draft." in docs[1]["content"] and "First draft." in docs[2]["content"]


def test_screening_answers_are_versioned_per_question(client, fake_llm, app_id):
    fake_llm.answer = {"text": "Yes.", "fact_ids_used": ["F2"]}
    a = draft(client, app_id, kind="screening_answer", question="Are you legally able to work in Canada?").json()
    b = draft(client, app_id, kind="screening_answer", question="Do you have Python experience?").json()
    assert a["version"] == 1 and b["version"] == 1


def test_draft_errors(client, fake_llm, app_id, db_path):
    assert draft(client, 999).status_code == 404
    assert client.get("/applications/999/documents").status_code == 404
    assert client.post("/documents/999/versions", json={"content": "x"}).status_code == 404
    fake_llm.answer = {"wrong": "shape"}
    assert draft(client, app_id).status_code == 502
    assert client.get(f"/applications/{app_id}/documents").json() == []  # nothing saved on failure


def test_draft_needs_profile(client, make_job):
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    assert draft(client, app_id).status_code == 404


def test_build_facts_covers_profile(profile_data):
    facts = build_facts(Profile(**profile_data))
    texts = " ".join(f.text for f in facts)
    assert facts[0].id == "F1"
    assert "Senior QA Engineer at Example Corp (fictional)" in texts
    assert "Skills: Python" in texts


def test_sentence_copied_from_posting_is_flagged(client, fake_llm, app_id):
    # The fictional posting mentions packaging services with Docker on AWS and deploying with customers;
    # the fictional profile has no such experience.
    fake_llm.answer = {
        "text": "At Example Corp I built a pytest suite for a payments API used by three internal teams. "
                "I have deployed our platform with customers and packaged services with Docker on AWS.",
        "fact_ids_used": ["F4"],
    }
    doc = draft(client, app_id).json()
    flagged = [w for w in doc["review"]["warnings"] if w.startswith("Removed from the letter, not backed by your profile")]
    assert len(flagged) == 1
    assert "deployed our platform with customers" in flagged[0]
    assert "deployed our platform with customers" not in doc["content"]          # removed from the letter itself
    assert "pytest suite for a payments API" in doc["content"]


def test_job_title_words_are_not_flagged_as_missing_skills(client, fake_llm, profile_data, make_job):
    client.put("/profile", json=profile_data)
    job = make_job(title="QA Team Lead", required_skills=["QA Team Lead", "Python"])
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.answer = {"text": "I'm applying for the QA Team Lead role.", "fact_ids_used": ["F1"]}
    warnings = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()["review"]["warnings"]
    assert not any("does not evidence" in w for w in warnings)


def test_letter_greeting_and_signature_come_from_the_app(client, fake_llm, app_id, profile_data):
    # The model adds its own greeting and a WRONG name; both are replaced.
    fake_llm.answer = {"text": "Dear Sir,\n\nI build pytest suites.\n\nBest regards,\nSomeone Else", "fact_ids_used": ["F4"]}
    content = draft(client, app_id).json()["content"]
    assert content.startswith("Dear Hiring Team,\n\nI'm applying for the ")
    assert content.endswith(f"I build pytest suites.\n\nSincerely,\n{profile_data['full_name']}")
    assert "Someone Else" not in content and "Dear Sir" not in content


def test_revision_removes_unsupported_sentence(client, fake_llm, app_id):
    fake_llm.queue = [
        {"text": "I built a pytest suite for a payments API. I have deployed our platform with customers "
                 "and packaged services with Docker on AWS.", "fact_ids_used": ["F4"]},
        {"text": "I built a pytest suite for a payments API used by three internal teams.", "fact_ids_used": ["F4"]},
    ]
    doc = draft(client, app_id).json()
    assert "AWS" not in doc["content"]
    assert doc["review"]["warnings"] == []
    assert doc["review"]["humanizer"]["revision_rounds"] == 1
    assert "NOT SUPPORTED BY THE FACTS" in fake_llm.calls[-1]["user"]


def test_opening_added_when_letter_does_not_name_the_job(client, fake_llm, app_id, job_data):
    fake_llm.answer = {"text": "At Example Corp I built a pytest suite.", "fact_ids_used": ["F4"]}
    content = draft(client, app_id).json()["content"]
    assert f"I'm applying for the {job_data['title']} role at {job_data['company']}. At Example Corp" in content


def test_opening_not_duplicated_when_model_names_the_company(client, fake_llm, app_id, job_data):
    body = f"I'm applying to {job_data['company']} because I build API tests.\n\nMore detail here."
    fake_llm.answer = {"text": body, "fact_ids_used": ["F4"]}
    content = draft(client, app_id).json()["content"]
    assert content.count(job_data["company"]) == 1


def test_letter_prompt_asks_for_opening_and_closing(client, fake_llm, app_id):
    fake_llm.answer = {"text": "x", "fact_ids_used": ["F1"]}
    draft(client, app_id)
    task = fake_llm.calls[0]["user"].split("TASK")[-1]
    assert "Opening" in task and "Closing" in task


def test_short_skill_names_like_csharp_are_checked(client, fake_llm, profile_data, make_job):
    client.put("/profile", json=profile_data)
    job = make_job(required_skills=["C#", "Python"], preferred_skills=["Go"])
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.answer = {"text": "I write Java and C# automation, and some Go.", "fact_ids_used": ["F1"]}
    warnings = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()["review"]["warnings"]
    flagged = next(w for w in warnings if "does not evidence" in w)
    assert "C#" in flagged and "Go" in flagged


def test_too_short_letter_is_expanded(client, fake_llm, app_id, monkeypatch):
    from app import drafting
    monkeypatch.setattr(drafting, "MIN_LETTER_WORDS", 150)
    short = {"text": "I build pytest suites for a payments API.", "fact_ids_used": ["F4"]}
    longer = {"text": " ".join(["I built a pytest suite for a payments API used by three internal teams."] * 12),
              "fact_ids_used": ["F4"]}
    fake_llm.queue = [short, longer]
    doc = draft(client, app_id).json()
    assert "TOO SHORT" in fake_llm.calls[1]["user"]
    assert len(doc["content"].split()) > 150


def test_job_term_backed_by_profile_evidence_is_not_flagged(client, fake_llm, profile_data, make_job):
    profile_data["experience"][0]["highlights"].append("Ran nightly regression suites in Jenkins.")
    client.put("/profile", json=profile_data)
    job = make_job(required_skills=["CI/CD pipelines", "C#"])
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    fake_llm.answer = {"text": "I run regression suites in CI/CD pipelines with Jenkins.", "fact_ids_used": ["F1"]}
    warnings = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()["review"]["warnings"]
    assert not any("does not evidence" in w for w in warnings)


# ---------- relevance: the letter is planned around the job's requirements ----------

def _two_role_profile():
    from app.schemas import Profile
    return Profile(
        full_name="Jordan Sample (FICTIONAL)",
        experience=[
            {"company": "CloudCo (fictional)", "title": "Automation Developer", "start_date": "2024-01",
             "highlights": ["Validated Kubernetes and Helm upgrades for distributed microservices.",
                            "Built Java API automation with REST Assured and JUnit in Jenkins pipelines."]},
            {"company": "BankCo (fictional)", "title": "ETL Tester", "start_date": "2020-01", "end_date": "2023-12",
             "highlights": ["Validated source-to-target mappings for anti-money-laundering reports.",
                            "Reconciled transformation rules with business analysts."]},
        ],
        education=[{"institution": "Uni (fictional)", "degree": "Bachelor's Degree"}],
    )


def _job(description):
    from datetime import datetime
    from app.schemas import JobOut
    return JobOut(id=1, title="SDET (fictional)", company="Example (fictional)", description=description,
                  required_skills=["placeholder (fictional)"], preferred_skills=[], created_at=datetime.now())


def test_plan_letter_puts_the_job_requirements_first_and_drops_unrelated_experience():
    from app.drafting import build_facts, plan_letter
    profile = _two_role_profile()
    job = _job("About us\nWe build things.\n\nRequired Qualifications:\n"
               "Experience with cloud and container technologies (AWS, Kubernetes, Docker)\n"
               "Hands-on experience with API testing tools (e.g., Postman or REST Assured)\n"
               "Bachelor's degree in Computer Science\n\nBenefits\nGood ones.")
    facts = build_facts(profile)
    priorities, shown = plan_letter(profile, job, facts)
    text = {f.id: f.text for f in facts}
    assert [p.requirement.split()[0] for p in priorities] == ["Experience", "Hands-on"]   # degree: not for a letter
    assert "Kubernetes" in text[priorities[0].fact_ids[0]]
    assert "REST Assured" in text[priorities[1].fact_ids[0]]
    assert not any(f.text.startswith("Education:") for f in shown)
    shown_text = " ".join(f.text for f in shown)
    assert "anti-money-laundering" not in shown_text and "Reconciled" not in shown_text   # irrelevant to this job
    assert "Role: ETL Tester at BankCo" in shown_text                                      # roles always available


def test_letter_prompt_lists_priorities(client, fake_llm, app_id):
    fake_llm.answer = {"text": "x", "fact_ids_used": ["F1"]}
    draft(client, app_id)
    assert "PRIORITIES (what this job needs most" in fake_llm.calls[-1]["user"]
