def test_create_list_and_get_job(client, job_data):
    created = client.post("/jobs", json=job_data)
    assert created.status_code == 201
    job = created.json()
    assert job["id"] == 1
    assert job["skills_source"] == "manual"

    assert [j["id"] for j in client.get("/jobs").json()] == [1]
    assert client.get("/jobs/1").json()["title"] == job_data["title"]


def test_list_jobs_empty(client):
    assert client.get("/jobs").json() == []


def test_get_missing_job_returns_404(client):
    response = client.get("/jobs/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job 999 not found."}


def test_non_positive_or_non_numeric_job_id_rejected(client):
    assert client.get("/jobs/0").status_code == 422
    assert client.get("/jobs/abc").status_code == 422


def test_job_requires_at_least_one_required_skill(client, job_data):
    job_data["required_skills"] = []
    assert client.post("/jobs", json=job_data).status_code == 422


def test_job_missing_fields_reports_each(client):
    response = client.post("/jobs", json={"title": "Only a title"})
    assert response.status_code == 422
    missing = {err["loc"][-1] for err in response.json()["detail"]}
    assert {"company", "description", "required_skills"} <= missing


def test_invalid_source_url_rejected(client, job_data):
    job_data["source_url"] = "not a url"
    assert client.post("/jobs", json=job_data).status_code == 422


def test_duplicate_source_url_rejected(client, job_data):
    first = client.post("/jobs", json=job_data).json()
    response = client.post("/jobs", json=job_data)
    assert response.status_code == 409
    assert response.json()["existing_id"] == first["id"]
    assert len(client.get("/jobs").json()) == 1


def test_same_title_and_company_is_flagged_as_duplicate(client, job_data):
    job_data.pop("source_url")
    first = client.post("/jobs", json=job_data).json()
    again = client.post("/jobs", json={**job_data, "title": job_data["title"].upper() + "  "})
    assert again.status_code == 409 and again.json()["existing_id"] == first["id"]
    forced = client.post("/jobs", json={**job_data, "allow_duplicate": True})
    assert forced.status_code == 201
