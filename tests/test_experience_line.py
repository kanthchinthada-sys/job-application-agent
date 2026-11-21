"""Closing a gap you really have: one line in your own words, added from the resume's gap list."""


def test_gap_line_goes_into_the_profile_and_skills(client, profile_data):
    client.put("/profile", json=profile_data)
    r = client.post("/profile/experience-line", json={
        "owner": "example corp (fictional)", "skill": "TestRail",
        "text": "Organised regression suites and release test runs in TestRail (FICTIONAL)."})
    assert r.status_code == 201 and r.json()["skill_added"] is True
    p = client.get("/profile").json()
    assert "TestRail" in p["skills"]
    assert any("TestRail" in h for h in p["experience"][0]["highlights"])


def test_gap_line_becomes_a_verified_bank_record(client, profile_data):
    from tests.test_bank import BANK, base_profile
    from app import bank
    p = base_profile()
    records, _ = bank.parse_bank(BANK, p)
    client.put("/profile", json=p.model_copy(update={"experience_bank": records}).model_dump(mode="json"))
    owner = records[0].owner
    r = client.post("/profile/experience-line", json={"owner": owner, "skill": "GitLab",
                                                      "text": "Ran the API suites in GitLab CI pipelines (FICTIONAL)."})
    assert r.json()["id"] == "ME01"
    rec = [x for x in client.get("/profile").json()["experience_bank"] if x["id"] == "ME01"][0]
    assert rec["status"] == "verified" and rec["owner"] == owner


def test_unknown_employer_is_refused(client, profile_data):
    client.put("/profile", json=profile_data)
    r = client.post("/profile/experience-line", json={"owner": "Nowhere Inc", "text": "Did something real here."})
    assert r.status_code == 404


def test_page_fitting_keeps_the_jobs_keywords():
    from app import layout
    from types import SimpleNamespace
    groups = [{"name": "Tools", "skills": [f"Tool{i}" for i in range(20)] + ["TestRail"]}]
    resume = SimpleNamespace(skill_groups=groups, experience=[], projects=[])
    layout.fit.__globals__["estimate_lines"], saved = (lambda r: 999), layout.estimate_lines
    try:
        layout.fit(resume, protect=["TestRail"], budget_override=10)
    finally:
        layout.fit.__globals__["estimate_lines"] = saved
    assert "TestRail" in resume.skill_groups[0]["skills"]
