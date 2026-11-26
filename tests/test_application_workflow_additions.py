from datetime import date, timedelta

from app import job_search


def test_technical_gap_review_creates_an_append_only_resume(client, profile_data, make_job, fake_llm):
    client.put("/profile", json=profile_data).raise_for_status()
    job = make_job(title="SDET", description="Required qualifications:\n- Hands-on experience with Cypress\n"
                   "- Excellent communication skills\n- A bachelor's degree",
                   required_skills=["Cypress", "Communication"], preferred_skills=[])
    pending = client.get("/experience-gaps", params={"job_id": job["id"]}).json()
    assert [item["skill"].lower() for item in pending] == ["cypress"]
    gap = pending[0]
    assert job["pending_technical_gaps"] == 1
    assert all("cypress" not in rec["text"].lower() for rec in client.get("/profile").json()["experience_bank"])
    assert fake_llm.calls == []

    app = client.post("/applications", json={"job_id": job["id"]}).json()
    base = client.post(f"/applications/{app['id']}/drafts", json={"kind": "resume"})
    base.raise_for_status()
    owner = profile_data["experience"][0]["company"]
    url = f"/experience-gaps/{gap['id']}/confirm"
    assert client.post(url, json={"owner": owner, "text": gap["example"], "confirmed": True}).status_code == 409
    assert client.post(url, json={"owner": owner, "text": "Built Selenium tests for checkout.", "confirmed": True}
                       ).status_code == 409
    body = {"owner": owner, "text": "Built Cypress regression tests for checkout and verified error responses.",
            "confirmed": True}
    approved = client.post(url, json=body)
    approved.raise_for_status()
    client.app.state.preparer.wait()
    docs = client.get(f"/applications/{app['id']}/documents").json()
    resumes = [d for d in docs if d["kind"] == "resume"]
    assert [d["version"] for d in resumes] == [2, 1]
    assert resumes[1]["id"] == base.json()["id"]
    assert "cypress" in resumes[0]["content"].lower()
    assert [r for r in client.get("/profile").json()["experience_bank"] if r["id"] == approved.json()["record_id"]
            ][0]["status"] == "verified"
    assert client.post(url, json=body).json()["record_id"] == approved.json()["record_id"]
    client.app.state.preparer.wait()
    assert len(client.get(f"/applications/{app['id']}/documents").json()) == len(docs)


def test_submitted_versions_and_scoped_edits_survive_new_drafts(client, profile_data, make_job):
    profile_data["experience_bank"] = [{"id": "EX01", "owner": profile_data["experience"][0]["company"],
        "owner_kind": "experience", "status": "verified", "text": "Built Playwright tests for the checkout API."}]
    client.put("/profile", json=profile_data).raise_for_status()
    job = make_job(required_skills=["Playwright"])
    app_id = client.post("/applications", json={"job_id": job["id"], "status": "submission_attempted"}).json()["id"]
    original = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    candidate = {"section": "summary", "text": "Quality engineer with experience in API and browser testing."}
    check = client.post(f"/documents/{original['id']}/section", json=candidate)
    assert check.status_code == 200, check.text
    assert check.json()["document"] is None
    updated = client.post(f"/documents/{original['id']}/section", json={**candidate, "save": True}).json()["document"]
    assert updated["version"] == 2
    confirm = client.patch(f"/applications/{app_id}", json={"status": "submitted_confirmed",
        "submission_confirmation": "Example confirmation", "submitted_resume_id": original["id"],
        "next_action": "Review interview questions", "due_date": str(date.today()),
        "interview_at": "2026-11-30T15:00:00Z"})
    assert confirm.status_code == 200, confirm.text
    assert client.get(f"/applications/{app_id}").json()["next_action"] == "Review interview questions"
    prep = client.get(f"/applications/{app_id}/interview-prep").json()
    assert prep["versions"]["resume"] == {"id": original["id"], "version": 1, "submitted": True}
    assert client.patch(f"/applications/{app_id}", json={"due_date": None}).json()["due_date"] is None
    assert client.get(f"/applications/{app_id}").json()["submitted_resume_id"] == original["id"]


def test_saved_search_tracks_new_jobs_from_successful_runs(client, monkeypatch):
    urls = ["https://jobs.example.com/a"]

    def search(title, country, start, end, *args, **kwargs):
        return job_search.SearchResult(query=title, country=country, date_from=str(start), date_to=str(end),
            results=[job_search.FoundJob(source="fixture", title=title, company="Test", location="Toronto",
                                        url=url, posted=str(end)) for url in urls],
            sources=[job_search.SourceStatus(name="Fixture", ok=True)])

    monkeypatch.setattr(job_search, "search", search)
    today = date.today()
    result = client.post("/search/saved", json={"name": "SDET Canada", "filters": {
        "title": "SDET", "country": "CA", "date_from": str(today - timedelta(days=7)), "date_to": str(today)}})
    assert result.status_code == 201, result.text
    run = f"/search/saved/{result.json()['id']}/run"
    assert not client.post(run).json()["results"][0]["new_since_last_run"]
    urls.append("https://jobs.example.com/b")
    assert [row["new_since_last_run"] for row in client.post(run).json()["results"]] == [False, True]
    assert client.get("/search/latest").json()["result"]["results"][1]["new_since_last_run"]


def test_provider_benchmark_reuses_only_the_matching_saved_key(client, db_path, fake_llm, monkeypatch):
    from app import ai_settings, llm, metrics

    ai_settings.save(db_path, "claude", "fake-claude-benchmark-key", "fictional-claude")
    created = {}

    class MeasuredFake(type(fake_llm)):
        def generate_json(self, system, user, schema):
            metrics.note_usage(input_tokens=100, output_tokens=30)
            if "examples" in schema.get("properties", {}):
                return {"examples": []}
            return super().generate_json(system, user, schema)

    def factory(provider):
        def make(key, model):
            created[provider] = (key, model)
            fake = MeasuredFake()
            fake.model = model or f"fictional-{provider}"
            return fake
        return make

    monkeypatch.setattr(llm, "ClaudeClient", factory("claude"))
    monkeypatch.setattr(llm, "OpenAIClient", factory("openai"))
    case = client.get("/ai/benchmark/cases").json()[0]
    assert client.post("/ai/benchmark", json={"case_id": case["id"]}).status_code == 422
    response = client.post("/ai/benchmark", json={"case_id": case["id"],
        "openai_key": "fake-openai-benchmark-key", "rates": {"claude": {"input": 3, "output": 15}}})
    assert response.status_code == 200, response.text
    assert created["claude"] == ("fake-claude-benchmark-key", "fictional-claude")
    assert created["openai"][0] == "fake-openai-benchmark-key"
    results = {r["provider"]: r for r in response.json()["results"]}
    assert results["claude"]["estimated_usd"] > 0
    assert results["openai"]["estimated_usd"] is None
    assert all(r["summary"] and r["letter"] for r in results.values())
    assert "fake-openai-benchmark-key" not in response.text
    assert ai_settings.load(db_path)["provider"] == "claude"


def test_gaps_are_worked_out_for_a_job_saved_before_they_could_be(client, profile_data, make_job, db_path):
    import sqlite3
    # saved before gap review ran for it (here: before the profile existed, so there was nothing to compare)
    job = make_job(title="SDET", description="Required qualifications:\n- Hands-on experience with Cypress",
                   required_skills=["Cypress"], preferred_skills=[])
    assert job.get("pending_technical_gaps", 0) == 0
    client.put("/profile", json=profile_data).raise_for_status()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM gap_suggestions").fetchone()[0] == 0
    # opening the job's application works them out once, so the banner and the review panel show
    assert client.get(f"/jobs/{job['id']}").json()["pending_technical_gaps"] == 1
    assert [g["skill"].lower() for g in client.get("/experience-gaps", params={"job_id": job["id"]}).json()] == ["cypress"]


def test_gap_prompts_keep_the_postings_spelling_and_say_what_would_count():
    from types import SimpleNamespace
    from app import gap_review
    job = SimpleNamespace(required_skills=["DevSecOps", "incident response"], preferred_skills=[],
                          description="Experience with DevSecOps and incident response.")
    assert gap_review._label("devsecops", job) == "DevSecOps"
    assert gap_review._label("incident response", job) == "Incident response"          # as a heading
    assert gap_review._label("incident response", job, heading=False) == "incident response"
    example = gap_review._example("incident response", "incident response")
    assert example.startswith("Draft only if true: Handled incident response for [system]")
    assert "complete a specific technical task" not in gap_review._example("scala", "Scala")


def test_combined_skills_are_checked_part_by_part():
    from app import gap_review
    assert gap_review._parts("Docker/GitHub Actions/Azure") == ["Docker", "GitHub Actions", "Azure"]
    assert gap_review._parts("version control (Git)") == ["Git"]
    assert gap_review._parts("CI/CD") == ["CI/CD"]


def test_a_skill_the_match_table_found_is_not_a_gap(client, profile_data, make_job):
    profile_data = {**profile_data, "skills": [*profile_data.get("skills", []), "JUnit"]}
    client.put("/profile", json=profile_data).raise_for_status()
    job = make_job(title="SDET", description="Required qualifications:\n- Experience with TestNG/JUnit and Cypress",
                   required_skills=["TestNG/JUnit", "Cypress"], preferred_skills=[])
    skills = [g["skill"] for g in client.get("/experience-gaps", params={"job_id": job["id"]}).json()]
    assert "Cypress" in skills                                   # missing, spelled as the posting spells it
    assert not any("TestNG" in s or "JUnit" in s for s in skills)   # either one meets it, and JUnit is there
