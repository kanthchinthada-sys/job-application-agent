def test_get_profile_before_saving_returns_404(client):
    response = client.get("/profile")
    assert response.status_code == 404
    assert "No profile saved yet" in response.json()["detail"]


def test_save_and_retrieve_profile(client, profile_data):
    saved = client.put("/profile", json=profile_data)
    assert saved.status_code == 200
    body = client.get("/profile").json()
    assert body["full_name"] == profile_data["full_name"]
    assert body["experience"][0]["company"] == "Example Corp (fictional)"
    assert body["preferences"]["target_roles"] == ["Forward Deployed Engineer", "Solutions Engineer"]
    assert "updated_at" in body


def test_put_replaces_whole_profile(client, profile_data):
    client.put("/profile", json=profile_data)
    client.put("/profile", json={"full_name": "Replacement (FICTIONAL)"})
    body = client.get("/profile").json()
    assert body["full_name"] == "Replacement (FICTIONAL)"
    assert body["skills"] == []  # old data is not silently merged in


def test_duplicate_skills_are_removed_case_insensitively(client):
    client.put("/profile", json={"full_name": "Test (FICTIONAL)", "skills": ["Python", " python ", "SQL"]})
    assert client.get("/profile").json()["skills"] == ["Python", "SQL"]


def test_profile_requires_name(client):
    response = client.put("/profile", json={"skills": ["Python"]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "full_name"]


def test_blank_name_rejected(client):
    assert client.put("/profile", json={"full_name": "   "}).status_code == 422


def test_unknown_field_rejected(client):
    response = client.put("/profile", json={"full_name": "X (FICTIONAL)", "skils": ["typo"]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_invalid_dates_rejected(client, profile_data):
    profile_data["experience"][1]["end_date"] = "2017-01"  # before its start_date
    assert client.put("/profile", json=profile_data).status_code == 422
    profile_data["experience"][1]["end_date"] = "2021-13"  # month 13
    assert client.put("/profile", json=profile_data).status_code == 422


def test_invalid_email_rejected(client):
    response = client.put("/profile", json={"full_name": "X (FICTIONAL)", "contact": {"email": "not-an-email"}})
    assert response.status_code == 422
