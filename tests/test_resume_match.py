import json
from contextlib import closing

from app import database, repository, resume_pdf_review


def _draft(client, db_path, profile, make_job):
    job = make_job(title="Test Engineer", required_skills=["Kubernetes integration testing", "Cypress", "Go", "Communication"],
                   preferred_skills=["PostgreSQL", "Postgres", "Kubernetes integration testing", "AWS"])
    application_id = client.post("/applications", json={"job_id": job["id"]}).json()["id"]
    role = profile["experience"][0]
    content = {"name": profile["full_name"], "headline": None, "contact": {},
               "summary": "Built Kubernetes tooling. Improved integration testing for checkout.", "summary_source": "profile",
               "skills": ["Postgres"], "experience": [{"company": role["company"], "title": role["title"],
               "location": role.get("location"), "start_date": role["start_date"], "end_date": role.get("end_date"),
               "bullets": [{"text": "Helped the team go through regression results.", "original": "Reviewed results.",
                            "changed": True}], "omitted": 0}],
               "projects": [{"name": "Fictional Tools", "description": "Built a local service.", "skills": []}],
               "education": [], "certifications": [], "achievements": []}
    with closing(database.connect(db_path)) as conn:
        return repository.add_document(conn, application_id, "resume", "", json.dumps(content), "ai", None, {})


def test_verified_match_preview_revalidates_approval_and_keeps_versions(client, db_path, profile_data, make_job, fake_llm):
    employer = profile_data["experience"][0]["company"]
    profile_data["experience_bank"] = [
        {"id": "EX01", "owner": employer, "owner_kind": "experience", "status": "verified",
         "text": "Built Kubernetes integration testing fixtures in Go to verify test namespace cleanup."},
        {"id": "EX02", "owner": employer, "owner_kind": "experience", "status": "unverified",
         "text": "Implemented Cypress regression tests on checkout."},
        {"id": "EX03", "owner": employer, "owner_kind": "experience", "status": "verified",
         "text": "Reviewed an AWS deployment design.", "caveat": "Design review only, without AWS access."},
    ]
    client.put("/profile", json=profile_data).raise_for_status()
    doc = _draft(client, db_path, profile_data, make_job)
    base = f"/documents/{doc.id}"
    report = client.get(f"{base}/technical-coverage").raise_for_status().json()
    assert report["score"] == 12.5
    assert report["required"] == {"total": 3, "covered": 0, "demonstrated": 0}
    assert report["preferred"] == {"total": 2, "covered": 1, "demonstrated": 0}
    assert [r["placement"] for r in report["rows"]] == ["verified", "gap", "verified", "skills", "profile"]
    evidence = f"{base}/verified-evidence"
    assert client.post(evidence, json={"keyword": "Cypress", "record_id": "EX02"}).status_code == 409
    assert client.post(evidence, json={"keyword": "AWS", "record_id": "EX03"}).status_code == 409
    request = {"keyword": "Kubernetes integration testing", "record_id": "EX01"}
    preview = client.post(evidence, json=request).raise_for_status().json()
    assert preview["document"] is None and preview["replaces"] is None
    assert preview["after"]["score"] == 62.5
    assert preview["after"]["required"]["demonstrated"] == 2
    assert client.post(evidence, json={**request, "approval": "1" * 64}).status_code == 409
    assert len(client.get(f"/applications/{doc.application_id}/documents").json()) == 1
    payload = {**request, "approval": preview["approval"]}
    saved = client.post(evidence, json=payload).raise_for_status().json()["document"]
    assert saved["version"] == 2
    assert saved["review"]["facts_used"] == ["EX01"]
    assert json.loads(saved["content"])["experience"][0]["bullets"][0]["text"] == preview["text"]
    assert client.post(evidence, json=payload).raise_for_status().json()["document"]["id"] == saved["id"]
    assert len(client.get(f"/applications/{doc.application_id}/documents").json()) == 2
    assert not fake_llm.calls


def test_pdf_check_reports_keyword_and_reading_order_loss(client, db_path, profile_data, make_job, monkeypatch):
    from app import resume_match, resume_render, tailoring
    from app.schemas import JobOut, Profile

    client.put("/profile", json=profile_data).raise_for_status()
    doc = _draft(client, db_path, profile_data, make_job)
    resume = tailoring.TailoredResume.model_validate_json(doc.content)
    resume.summary = "Used Go and Cypress to test Kubernetes integration testing infrastructure."
    app = client.get(f"/applications/{doc.application_id}").json()
    job = JobOut.model_validate(client.get(f"/jobs/{app['job_id']}").json())
    coverage = resume_match.analyze(resume, Profile.model_validate(profile_data), job)
    role = resume.experience[0]
    dates = f"{resume_render._month(role.start_date)} - {resume_render._month(role.end_date)}"
    employer = role.company + (f" | {role.location}" if role.location else "")
    extracted = (f"CORE SKILLS\nPostgres\nPROFESSIONAL SUMMARY\nGo and Kubernetes integration\n testing\n"
                 f"PROFESSIONAL EXPERIENCE\n{employer}\n{dates}\n{role.title}\nPROJECTS\nFictional Tools")
    monkeypatch.setattr(resume_pdf_review.resume_import, "extract_text", lambda name, data: extracted)
    result = resume_pdf_review.check(b"%PDF fixture", resume, coverage)
    assert result.readable
    assert result.required_found == ["Kubernetes integration testing", "Go"]
    assert result.required_missing == ["Cypress"]
    assert result.issues == ["Section headings came out in a different reading order."]
