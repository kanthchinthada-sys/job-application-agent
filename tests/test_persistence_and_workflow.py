from fastapi.testclient import TestClient

from app.api import create_app


def test_data_survives_restart(db_path, profile_data, job_data, fake_llm):
    """Simulate a server restart: build a second app instance on the same file."""
    with TestClient(create_app(db_path, llm=fake_llm)) as first:
        first.put("/profile", json=profile_data)
        job_id = first.post("/jobs", json=job_data).json()["id"]
        app_id = first.post("/applications", json={"job_id": job_id}).json()["id"]
        first.patch(f"/applications/{app_id}", json={"status": "ready_for_review"})

    with TestClient(create_app(db_path, llm=fake_llm)) as second:
        assert second.get("/profile").json()["full_name"] == profile_data["full_name"]
        assert second.get(f"/jobs/{job_id}").json()["title"] == job_data["title"]
        app = second.get(f"/applications/{app_id}").json()
        assert app["status"] == "ready_for_review"
        assert len(app["history"]) == 2
        # duplicate protection also survives the restart
        assert second.post("/applications", json={"job_id": job_id}).status_code == 409


def test_tests_use_temporary_database(db_path, tmp_path):
    assert db_path.parent == tmp_path
    assert "data" not in db_path.parts[-2:]


def test_full_workflow(client, profile_data, job_data):
    """Profile -> job -> match -> application -> status updates -> tracker list."""
    assert client.get("/health").json()["status"] == "ok"
    assert client.put("/profile", json=profile_data).status_code == 200

    job = client.post("/jobs", json=job_data).json()
    report = client.get(f"/jobs/{job['id']}/match").json()
    assert report["required"]["total"] == len(job_data["required_skills"])

    app = client.post("/applications", json={"job_id": job["id"]}).json()
    for status in ["ready_for_review", "submission_attempted", "submission_uncertain"]:
        app = client.patch(f"/applications/{app['id']}", json={"status": status}).json()
    app = client.patch(
        f"/applications/{app['id']}",
        json={"status": "submitted_confirmed", "submission_confirmation": "FAKE-CONF-001"},
    ).json()

    tracker = client.get("/applications").json()
    assert len(tracker) == 1
    assert tracker[0]["status"] == "submitted_confirmed"
    assert tracker[0]["source_url"] == job_data["source_url"]
    assert len(tracker[0]["history"]) == 5


def test_docs_and_openapi_available(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/profile", "/jobs", "/jobs/{job_id}/match", "/applications", "/applications/{application_id}"} <= set(paths)


def test_home_page_and_example_profile_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "Job Application Agent" in page.text
    assert "FICTIONAL" in client.get("/static/fictional_profile.json").json()["full_name"]
