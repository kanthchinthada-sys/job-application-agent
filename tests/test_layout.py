"""Two-page resumes: the page estimate, trimming order, minimums, and role-specific skill headings. FICTIONAL."""

import json

import pytest

from app import layout, selection
from app.tailoring import Bullet, Role, TailoredResume

from tests.test_bank import AI_JOB, BANK, LEAD_JOB, SDET_JOB, with_bank


def big_resume(roles=(14, 12, 8, 6), project=12, skills_per_group=25, groups=10) -> TailoredResume:
    long = "Validated fictional microservices across cloud and edge environments using REST Assured and JUnit 5 " \
           "while checking readiness, logs and pod events for each release (fictional)."
    exp = [Role(company=f"Company {i} (fictional)", title="Engineer", start_date="2020-01", end_date=None,
                location=None, recognition=None, omitted=0,
                bullets=[Bullet(text=f"{long} #{j}", original=long, changed=False, source_id=f"AG{i}{j:02d}")
                         for j in range(n)])
           for i, n in enumerate(roles)]
    return TailoredResume(
        name="Jordan Sample (FICTIONAL)", headline=None, contact={"email": "jordan@example.com"},
        summary="Fictional summary sentence. " * 6, summary_source="composed",
        skills=[f"Skill {g}-{k}" for g in range(groups) for k in range(skills_per_group)],
        skill_groups=[{"name": f"Group {g}", "skills": [f"Skill {g}-{k}" for k in range(skills_per_group)]}
                      for g in range(groups)],
        experience=exp,
        projects=[{"name": "Agent (fictional)", "description": "x", "skills": ["Python"],
                   "highlights": [f"{long} project {j}" for j in range(project)]}],
        education=[{"degree": "Bachelor's Degree", "institution": "Uni (fictional)"}],
        certifications=["Cert A (fictional)", "Cert B (fictional)"], achievements=[],
    )


def test_estimate_grows_with_content():
    small, big = big_resume(roles=(3, 2), project=2, groups=2), big_resume()
    assert layout.estimate_pages(small) < 1.2 < 2.5 < layout.estimate_pages(big)


def test_fit_brings_a_long_resume_to_two_pages_and_keeps_minimums():
    r = big_resume()
    scores = {b.source_id: 10 - j * 0.1 for role in r.experience for j, b in enumerate(role.bullets)}
    report = layout.fit(r, scores)
    assert report["fits"] and layout.estimate_pages(r) <= 2
    counts = [len(role.bullets) for role in r.experience]
    assert all(c >= m for c, m in zip(counts, layout.MIN_BULLETS))
    assert counts[0] >= counts[-1]                              # older roles give way first
    assert report["removed_bullets"]


def test_least_relevant_bullets_go_first():
    r = big_resume(roles=(14,), project=0, groups=2)
    scores = {b.source_id: (100 if j == 13 else 1) for j, b in enumerate(r.experience[0].bullets)}
    layout.fit(r, scores, max_pages=1)
    assert r.experience[0].bullets[-1].source_id == "AG013"    # the most relevant one survived, though listed last


def test_a_short_resume_is_left_alone():
    r = big_resume(roles=(3, 2), project=2, groups=2)
    before = r.model_dump()
    report = layout.fit(r)
    assert report["removed_bullets"] == [] and r.model_dump() == before


def test_at_most_seven_skill_headings_chosen_for_the_role():
    groups = ["Quality engineering and leadership", "Test automation", "Programming languages",
              "Cloud and distributed systems", "CI/CD and build tools", "Data engineering and validation",
              "Performance and observability", "AI applications and testing", "AI-assisted development",
              "Tools and delivery practices"]
    p = with_bank(skill_groups=[{"name": g, "skills": [f"{g.split()[0]} skill (fictional)", "Python"]} for g in groups])
    for job, must in [(AI_JOB, "AI applications and testing"), (LEAD_JOB, "Quality engineering and leadership"),
                      (SDET_JOB, "Test automation")]:
        plan = selection.plan(p, job)
        _, chosen = selection.select_skills(p, job, plan)
        names = [g["name"] for g in chosen]
        assert len(names) <= 7, names
        assert must in names, (job.title, names)


def test_tools_from_dropped_headings_move_to_a_kept_heading():
    groups = [{"name": f"Concepts {i} (fictional)", "skills": [f"concept {i}"]} for i in range(8)]
    groups.append({"name": "Cloud and distributed systems", "skills": ["Kubernetes"]})
    groups.append({"name": "Niche heading (fictional)", "skills": ["Linkerd"]})
    p = with_bank(skill_groups=groups)
    plan = selection.plan(p, SDET_JOB)
    _, chosen = selection.select_skills(p, SDET_JOB, plan)
    all_skills = [s for g in chosen for s in g["skills"]]
    assert len(chosen) <= 7
    assert "Linkerd" in all_skills                              # named tool kept, under a heading that stayed


def test_tailored_resume_reports_its_layout(client, fake_llm):
    p = with_bank()
    client.put("/profile", json=json.loads(p.model_dump_json()))
    job_id = client.post("/jobs", json={"title": "Senior SDET", "company": "Example (fictional)",
                                        "description": SDET_JOB.description, "required_skills": ["Java"],
                                        "allow_duplicate": True}).json()["id"]
    app_id = client.post("/applications", json={"job_id": job_id}).json()["id"]
    fake_llm.answer = {"bullets": [], "sentences": []}
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
    assert doc["review"]["layout"]["fits"] and doc["review"]["layout"]["estimated_pages"] <= 2
