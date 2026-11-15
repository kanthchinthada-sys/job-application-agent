import pytest

def test_create_and_get_application(client, make_job):
    job = make_job()
    response = client.post("/applications", json={"job_id": job["id"], "notes": "Referral from a friend"})
    assert response.status_code == 201
    app = response.json()
    assert app["status"] == "preparing"
    assert app["company"] == job["company"]
    assert app["history"][0]["to_status"] == "preparing"
    assert client.get(f"/applications/{app['id']}").json() == app


def test_application_for_missing_job_returns_404(client):
    response = client.post("/applications", json={"job_id": 77})
    assert response.status_code == 404
    assert client.get("/applications").json() == []


def test_duplicate_application_for_same_job_rejected(client, make_job):
    job = make_job()
    first = client.post("/applications", json={"job_id": job["id"]}).json()
    response = client.post("/applications", json={"job_id": job["id"]})
    assert response.status_code == 409
    assert response.json()["existing_id"] == first["id"]
    assert len(client.get("/applications").json()) == 1


def test_get_and_patch_missing_application_return_404(client):
    assert client.get("/applications/5").status_code == 404
    assert client.patch("/applications/5", json={"notes": "x"}).status_code == 404


def test_invalid_status_rejected(client, make_job):
    job = make_job()
    assert client.post("/applications", json={"job_id": job["id"], "status": "submitted"}).status_code == 422
    app_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    assert client.patch(f"/applications/{app_id}", json={"status": "done"}).status_code == 422


def test_status_changes_are_recorded_in_history(client, make_job):
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    client.patch(f"/applications/{app_id}", json={"status": "ready_for_review"})
    updated = client.patch(
        f"/applications/{app_id}", json={"status": "submission_attempted", "event_note": "Clicked submit"}
    ).json()
    assert [(e["from_status"], e["to_status"]) for e in updated["history"]] == [
        (None, "preparing"),
        ("preparing", "ready_for_review"),
        ("ready_for_review", "submission_attempted"),
    ]
    assert updated["history"][-1]["note"] == "Clicked submit"


def test_notes_only_patch_keeps_status_and_adds_no_history(client, make_job):
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    updated = client.patch(f"/applications/{app_id}", json={"notes": "Follow up Friday"}).json()
    assert updated["status"] == "preparing"
    assert updated["notes"] == "Follow up Friday"
    assert len(updated["history"]) == 1


def test_cannot_confirm_submission_without_evidence(client, make_job):
    job = make_job()
    # On create: body validation -> 422
    response = client.post("/applications", json={"job_id": job["id"], "status": "submitted_confirmed"})
    assert response.status_code == 422

    app_id = client.post("/applications", json={"job_id": job["id"], "status": "submission_attempted"}).json()["id"]
    # On update: depends on stored state -> 409, and nothing changes
    response = client.patch(f"/applications/{app_id}", json={"status": "submitted_confirmed"})
    assert response.status_code == 409
    assert client.get(f"/applications/{app_id}").json()["status"] == "submission_attempted"

    confirmed = client.patch(
        f"/applications/{app_id}",
        json={"status": "submitted_confirmed", "submission_confirmation": "Confirmation email #FAKE-123"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["submission_confirmation"] == "Confirmation email #FAKE-123"


def test_filter_applications_by_status(client, make_job):
    a = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    client.post("/applications", json={"job_id": make_job()["id"]})
    client.patch(f"/applications/{a}", json={"status": "submission_uncertain"})
    result = client.get("/applications", params={"status": "submission_uncertain"}).json()
    assert [r["id"] for r in result] == [a]
    assert client.get("/applications", params={"status": "bogus"}).status_code == 422


@pytest.mark.parametrize("path, ok", [
    (["ready_for_review", "preparing"], True),                         # back to editing
    (["submission_attempted", "ready_for_review"], True),             # retry after a failed submit
    (["submitted_confirmed", "preparing"], False),                    # can't un-submit
    (["submitted_confirmed", "interviewing", "offer"], True),
    (["rejected"], False),                                            # can't be rejected before applying
    (["withdrawn", "preparing"], True),                               # re-open
])
def test_status_transitions(client, make_job, path, ok):
    app_id = client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]
    codes = []
    for status in path:
        body = {"status": status}
        if status == "submitted_confirmed":
            body["submission_confirmation"] = "FAKE-1"
        codes.append(client.patch(f"/applications/{app_id}", json=body).status_code)
    assert (codes[-1] == 200) is ok
    assert all(c == 200 for c in codes[:-1])


def test_rejected_is_final_and_allowed_next_is_reported(client, make_job):
    app = client.post("/applications", json={"job_id": make_job()["id"]}).json()
    assert "rejected" not in app["allowed_next"] and "ready_for_review" in app["allowed_next"]
    client.patch(f"/applications/{app['id']}", json={"status": "submission_attempted"})
    rejected = client.patch(f"/applications/{app['id']}", json={"status": "rejected"}).json()
    assert rejected["allowed_next"] == []
    response = client.patch(f"/applications/{app['id']}", json={"status": "interviewing"})
    assert response.status_code == 409 and "final status" in response.json()["detail"]
