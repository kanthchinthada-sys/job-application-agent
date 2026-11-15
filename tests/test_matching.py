"""Unit tests for the pure matching module, plus the /jobs/{id}/match endpoint."""

from app.matching import compare_skills, normalize
from app.schemas import Profile


def make_profile(**kwargs):
    return Profile(full_name="Test (FICTIONAL)", **kwargs)


def test_normalize_case_whitespace_and_aliases():
    assert normalize("  PyThon ") == "python"
    assert normalize("REST   APIs") == "rest apis"
    assert normalize("k8s") == "kubernetes"
    assert normalize("Postgres") == "postgresql"


def test_declared_skill_matches_with_evidence_source():
    profile = make_profile(
        skills=["Python"],
        experience=[{"company": "Ex (fictional)", "title": "QA", "start_date": "2020-01", "skills": ["Postgres"]}],
    )
    result = compare_skills(profile, ["python", "PostgreSQL"])
    assert [m.skill for m in result.matched] == ["python", "PostgreSQL"]
    assert result.matched[1].evidence[0].source == "skills used at Ex (fictional)"
    assert result.matched[1].evidence[0].via == "postgres"
    assert result.coverage_percent == 100.0


def test_experience_written_in_sentences_counts():
    profile = make_profile(experience=[{"company": "Ex (fictional)", "title": "QA", "start_date": "2020-01",
                                        "highlights": ["Reproduced bugs using Docker containers."]}])
    result = compare_skills(profile, ["Docker"])
    assert [m.skill for m in result.matched] == ["Docker"]
    assert result.matched[0].evidence[0].text == "Reproduced bugs using Docker containers."


def test_combined_requirement_is_split_and_lists_what_is_missing():
    profile = make_profile(skills=["Python", "Java", "Go"])
    result = compare_skills(profile, ["Python/Java/Scala/go"])
    m = result.matched[0]
    assert m.found_parts == ["python", "java", "go"] and m.missing_parts == ["scala"]


def test_related_experience_counts_but_is_marked_for_checking():
    profile = make_profile(experience=[{"company": "Bank (fictional)", "title": "ETL Tester", "start_date": "2020-01",
                                        "highlights": ["Validated ETL source-to-target mappings in Azure."]}])
    result = compare_skills(profile, ["Data pipelines", "Databricks"])
    assert [m.skill for m in result.related] == ["Data pipelines"]
    assert result.related[0].evidence[0].via == "etl" and result.related[0].evidence[0].kind == "related"
    assert result.missing == ["Databricks"]                       # never invented
    assert result.coverage_percent == 50.0


def test_degree_is_checked_against_education():
    profile = make_profile(education=[{"institution": "Uni (fictional)", "degree": "Bachelor's Degree"}])
    assert [m.skill for m in compare_skills(profile, ["Bachelor's degree"]).matched] == ["Bachelor's degree"]
    assert compare_skills(profile, ["Master's degree"]).missing == ["Master's degree"]


def test_requirement_phrasing_is_understood():
    profile = make_profile(skills=["Postman", "API testing", "Jenkins", "Kubernetes", "TestNG"])
    result = compare_skills(profile, ["API testing tools", "CI/CD pipelines", "Container technologies",
                                      "Automated test frameworks", "Agile methodologies"])
    assert {m.skill for m in result.matched + result.related} == {
        "API testing tools", "CI/CD pipelines", "Container technologies", "Automated test frameworks"}
    assert result.missing == ["Agile methodologies"]


def test_mentions_use_word_boundaries():
    profile = make_profile(summary="Strong in JavaScript.")
    result = compare_skills(profile, ["Java"])
    assert result.missing == ["Java"]


def test_missing_and_empty_sections():
    profile = make_profile(skills=["SQL"])
    assert compare_skills(profile, ["AWS"]).missing == ["AWS"]
    empty = compare_skills(profile, [])
    assert empty.total == 0 and empty.coverage_percent is None


def test_comparison_is_deterministic(profile_data, job_data):
    profile = Profile(**profile_data)
    first = compare_skills(profile, job_data["required_skills"])
    second = compare_skills(profile, job_data["required_skills"])
    assert first == second


def test_match_endpoint_on_fictional_data(client, profile_data, job_data):
    client.put("/profile", json=profile_data)
    job_id = client.post("/jobs", json=job_data).json()["id"]

    report = client.get(f"/jobs/{job_id}/match").json()
    assert report["method"] == "evidence_match_v2"
    assert "Rule-based evidence comparison" in report["method_label"] and "Not an AI" in report["method_label"]
    assert report["limitations"]

    req = report["required"]
    assert [m["skill"] for m in req["matched"]] == ["Python", "REST APIs", "PostgreSQL", "Docker"]
    assert req["missing"] == ["AWS", "Customer communication"]
    assert req["coverage_percent"] == 66.7

    pref = report["preferred"]
    assert [m["skill"] for m in pref["matched"]] == ["pytest"]
    assert pref["missing"] == ["TypeScript", "Kubernetes"]


def test_match_without_profile_returns_404(client, job_data):
    job_id = client.post("/jobs", json=job_data).json()["id"]
    response = client.get(f"/jobs/{job_id}/match")
    assert response.status_code == 404
    assert "profile" in response.json()["detail"].lower()


def test_match_for_missing_job_returns_404(client, profile_data):
    client.put("/profile", json=profile_data)
    assert client.get("/jobs/42/match").status_code == 404


# ---------- AI second look ----------

def _setup_ai_job(client, profile_data, job_data):
    client.put("/profile", json=profile_data)
    return client.post("/jobs", json=job_data).json()["id"]


def test_ai_second_look_keeps_only_quotes_really_in_the_profile(client, fake_llm, profile_data, job_data):
    from app.drafting import build_facts
    from app.schemas import Profile
    job_id = _setup_ai_job(client, profile_data, job_data)
    facts = build_facts(Profile(**profile_data))
    real = next(f for f in facts if len(f.text.split()) > 8)
    real_quote = " ".join(real.text.split()[2:7])
    fake_llm.answer = {"answers": [
        {"requirement": "AWS", "fact_id": real.id, "quote": real_quote, "reason": "related (fictional)"},
        {"requirement": "Customer communication", "fact_id": real.id, "quote": "talked to customers daily",
         "reason": "made up"},                                            # not in the fact: thrown away
        {"requirement": "Kubernetes", "fact_id": "F999", "quote": "ran clusters", "reason": "unknown fact"},
    ]}
    report = client.post(f"/jobs/{job_id}/match/ai").json()
    assert [s["requirement"] for s in report["suggestions"]] == ["AWS"]
    assert report["suggestions"][0]["fact"] == real.text
    assert report["rejected"] == 2
    assert "Customer communication" in report["still_missing"] and "Kubernetes" in report["still_missing"]
    assert "AWS" in fake_llm.calls[0]["user"] and "Python" not in fake_llm.calls[0]["user"].split("CANDIDATE")[0]


def test_ai_second_look_does_not_change_the_score(client, fake_llm, profile_data, job_data):
    job_id = _setup_ai_job(client, profile_data, job_data)
    before = client.get(f"/jobs/{job_id}/match").json()
    fake_llm.answer = {"answers": []}
    client.post(f"/jobs/{job_id}/match/ai")
    assert client.get(f"/jobs/{job_id}/match").json() == before


def test_ai_second_look_when_ollama_is_off(client, fake_llm, profile_data, job_data):
    from app.llm import LLMUnavailableError
    job_id = _setup_ai_job(client, profile_data, job_data)
    fake_llm.error = LLMUnavailableError("Cannot reach Ollama")
    assert client.post(f"/jobs/{job_id}/match/ai").status_code == 503
