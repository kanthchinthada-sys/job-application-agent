"""Manual submission handoff: aggregates the latest materials; never submits anything."""


def test_handoff_collects_latest_materials(client, fake_llm, profile_data, make_job):
    profile_data["contact"]["phone"] = "+1 555 0100"
    profile_data["preferences"]["work_authorization"] = "FICTIONAL: authorized"
    client.put("/profile", json=profile_data)
    job = make_job()
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]

    fake_llm.answer = {"text": "First body.", "fact_ids_used": ["F1"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"})
    fake_llm.answer = {"text": "Second body.", "fact_ids_used": ["F1"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"})
    fake_llm.answer = {"text": "Yes.", "fact_ids_used": ["F2"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "screening_answer", "question": "Can you work in Canada?"})

    h = client.get(f"/applications/{app_id}/handoff").json()
    assert h["apply_url"] == job["source_url"]
    assert "Second body." in h["cover_letter"]["content"] and h["cover_letter"]["version"] == 2
    assert [a["question"] for a in h["screening_answers"]] == ["Can you work in Canada?"]
    labels = {f["label"]: f["value"] for f in h["copy_fields"]}
    assert labels["Phone"] == "+1 555 0100" and labels["Work authorization"] == "FICTIONAL: authorized"
    assert any("No tailored resume" in w for w in h["warnings"])
    assert any("submission uncertain" in step for step in h["checklist"])
    assert client.get(f"/applications/{app_id}").json()["status"] == "preparing"   # nothing was submitted


def test_handoff_warns_when_already_submitted(client, profile_data, make_job):
    client.put("/profile", json=profile_data)
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    client.patch(f"/applications/{app_id}", json={"status": "submitted_confirmed", "submission_confirmation": "FAKE-9"})
    assert any("Don't apply twice" in w for w in client.get(f"/applications/{app_id}/handoff").json()["warnings"])


def test_handoff_404s(client, make_job):
    assert client.get("/applications/999/handoff").status_code == 404
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    assert client.get(f"/applications/{app_id}/handoff").status_code == 404   # no profile yet
