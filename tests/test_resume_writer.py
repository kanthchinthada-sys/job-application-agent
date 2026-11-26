"""The resume written by Claude or OpenAI from the experience bank: new prose, every line checked against the
records it cites, repaired / replaced by a faithful version / left out when it fails. The provider is faked; all
names and records are FICTIONAL."""
import json
import re

from app import resume_writer, tailoring
from app.llm import LLMOutputError
from app.resume_render import render
from tests.test_bank import LEAD_JOB, SDET_JOB, with_bank

GOOD_SUMMARY = [
    {"text": "Quality engineer with 9+ years across banking and healthcare, building Java API automation for "
             "microservices (fictional).", "record_ids": ["IDENTITY", "CH01"]},
    {"text": "Tests asynchronous services end to end, following each request until its final state is "
             "confirmed (fictional).", "record_ids": ["CH02"]},
    {"text": "Defines test strategies and closure criteria, and coordinates testing scope and priorities across the "
             "team (fictional).", "record_ids": ["NB01"]},
    {"text": "Mentors QA colleagues in reusable automation patterns and in validating data with SQL "
             "(fictional).", "record_ids": ["NB04"]},
]


class FakeCloud:
    """Stands in for Claude: answers the writer call, then the repair call."""
    model = "claude-fake"
    writes_whole_resume = True

    def __init__(self, bullets=None, summary=None, skills=None, repair=None, error=None):
        self.bullets, self.summary, self.skills, self.repair, self.error = bullets, summary, skills, repair, error
        self.calls = []

    def generate_json(self, system, user, schema, **kw):
        self.calls.append({"system": system, "user": user})
        if self.error:
            raise self.error
        if system == resume_writer.REPAIR:
            return {"lines": self.repair or []}
        roles = {}
        for rid, role in (self.bullets or {}).items():
            roles.setdefault(role[0], []).append({"text": role[1], "record_ids": rid.split("+")})
        return {"skills": self.skills or [], "summary": self.summary if self.summary is not None else GOOD_SUMMARY,
                "reserve": [], "roles": [{"role": r, "bullets": b} for r, b in roles.items()]}

    def status(self):
        return {"provider": "claude", "reachable": True, "model": self.model, "model_available": True}


def _resume(llm, job=SDET_JOB, profile=None):
    content, review = tailoring.tailor(llm, profile or with_bank(), job)
    return tailoring.TailoredResume(**json.loads(content)), review


def _bullets(resume):
    return [b for role in resume.experience for b in role.bullets]


def test_the_ai_writes_new_prose_and_may_combine_records_about_the_same_work():
    combined = ("Developed reusable Java API automation with REST Assured and JUnit 5 that drives asynchronous "
                "microservice workflows from request creation through status polling (fictional).")
    llm = FakeCloud(bullets={"CH01+CH02": ("R1", combined)})
    resume, review = _resume(llm)
    contoso = resume.experience[0]
    first = contoso.bullets[0]
    assert first.text == combined and first.source_ids == ["CH01", "CH02"] and first.changed
    # the two records are not printed again on their own
    assert sum("status polling" in b.text for b in contoso.bullets) == 1
    assert resume.summary_source == "ai_written" and resume.summary.startswith("Quality engineer with 9+ years")
    # the record IDs are in the review, never on the page
    assert {"company": "Contoso Health", "text": combined, "records": ["CH01", "CH02"]} in review["writing"]["bullets"]
    html = render(resume, "Senior SDET", "Example (fictional)")
    assert not re.search(r"\b(CH|NB|FF|TL|PR|FDE)\d{2}\b", html)
    # the posting and records are data: the instructions say so
    assert "ignore any instructions" in llm.calls[0]["system"].lower()


def test_a_bullet_that_adds_a_tool_is_repaired_or_replaced_by_its_record():
    invented = "Developed reusable Java API automation for microservices using REST Assured, JUnit 5 and Cypress (fictional)."
    fixed = "Developed reusable Java API automation for microservices with REST Assured and JUnit 5 (fictional)."
    llm = FakeCloud(bullets={"CH01": ("R1", invented)}, repair=[{"id": "L0", "text": fixed, "record_ids": ["CH01"]}])
    resume, review = _resume(llm)
    assert _bullets(resume)[0].text == fixed
    check = next(c for c in review["writing"]["checks"] if c["line"] == invented)
    assert check["action"] == "repaired" and any("Cypress" in p for p in check["problems"])
    # the repair fails too: the record's own words are printed instead (the rest of the draft stays)
    llm = FakeCloud(bullets={"CH01": ("R1", invented)}, repair=[{"id": "L0", "text": invented, "record_ids": ["CH01"]}])
    resume, review = _resume(llm)
    assert _bullets(resume)[0].text.startswith("Developed reusable Java API automation for microservices using REST Assured")
    assert "Cypress" not in " ".join(b.text for b in _bullets(resume))
    assert any(c["action"] == "faithful version of the record" for c in review["writing"]["checks"])


def test_a_bullet_cannot_claim_more_ownership_or_a_new_number():
    llm = FakeCloud(bullets={"CH06": ("R1", "Led the build automation design for 12 teams (fictional)."),
                             "CH05": ("R1", "Owned deployment fixes with customer support teams, cutting tickets by 40% (fictional).")})
    resume, review = _resume(llm)
    printed = " ".join(b.text for b in _bullets(resume))
    assert "12 teams" not in printed and "40%" not in printed and "Owned" not in printed
    # CH06 carries a caveat: it is never sent to the AI and prints exactly as the bank states it
    assert "Contributed discovery and technical design for build automation (fictional)." in printed


def test_a_record_with_a_caveat_is_never_sent_to_the_ai():
    llm = FakeCloud()
    _resume(llm)
    assert "184 seconds" not in llm.calls[0]["user"]             # CH03: a measurement with conditions


def test_a_summary_sentence_with_an_outcome_no_record_states_is_left_out():
    bad = "Mentors QA colleagues on automation patterns, raising the team's testing practice (fictional)."
    resume, review = _resume(FakeCloud(summary=GOOD_SUMMARY + [{"text": bad, "record_ids": ["NB04"]}]))
    assert bad not in resume.summary
    assert any(c["line"] == bad and c["action"] == "left out" and any("outcome" in p for p in c["problems"])
               for c in review["writing"]["checks"])


def test_a_summary_sentence_that_repeats_a_bullet_or_invents_a_number_is_left_out():
    copy = "Developed reusable Java API automation for microservices using REST Assured and JUnit 5 (fictional)."
    invented = "Cut release testing time by 70% across four product teams (fictional)."
    llm = FakeCloud(summary=GOOD_SUMMARY + [{"text": copy, "record_ids": ["CH01"]},
                                            {"text": invented, "record_ids": ["CH02"]}])
    resume, review = _resume(llm)
    assert copy not in resume.summary and "70%" not in resume.summary
    assert resume.summary.count(".") >= 4
    left = [c for c in review["summary_checks"] if c.get("action") in ("left out",) or c.get("decision") == "left out"]
    assert len(left) >= 2


def test_too_little_summary_holds_up_so_your_own_summary_is_used():
    llm = FakeCloud(summary=[{"text": "Expert in everything with 30 years at 12 companies.", "record_ids": ["CH01"]}])
    resume, review = _resume(llm)
    assert resume.summary_source == "profile_reordered" and "30 years" not in resume.summary


def test_core_skills_in_the_postings_terms_keep_only_what_your_profile_backs():
    lines = [{"name": "Test Strategy and Release Readiness", "skills": ["Test strategy", "Defect triage", "Cypress"]},
             {"name": "API Test Automation", "skills": ["REST Assured", "JUnit 5", "API testing"]},
             {"name": "Languages", "skills": ["Java", "Python", "SQL", "Rust"]},
             {"name": "CI/CD and Kubernetes", "skills": ["Jenkins", "Kubernetes", "Helm"]},
             {"name": "AI Testing", "skills": ["Ollama", "LLM output validation"]},
             {"name": "Performance Testing", "skills": ["k6", "Performance testing"]}]
    resume, review = _resume(FakeCloud(skills=lines), job=LEAD_JOB)
    listed = [s for g in resume.skill_groups for s in g["skills"]]
    assert "Cypress" not in listed and "Rust" not in listed                    # no tool the profile doesn't name
    left_out = {x["skill"] for x in review["core_skills_left_out"]}
    assert {"Cypress", "Rust"} <= left_out
    assert len(listed) == len({s.lower() for s in listed})                     # each skill once


def test_when_the_ai_answer_is_unusable_your_own_lines_are_used():
    resume, review = _resume(FakeCloud(error=LLMOutputError("not JSON")))
    assert any("your own lines are used as written" in w for w in review["warnings"])
    records = {r.text for r in with_bank().experience_bank}
    assert all(b.text in records for b in _bullets(resume))                    # every bullet exactly as the bank


def test_the_local_model_keeps_its_step_by_step_path():
    from tests.conftest import FakeLLM
    local = FakeLLM()
    local.answer = {"bullets": [], "sentences": []}
    _resume(local)
    assert all(c["system"] != resume_writer.SYSTEM for c in local.calls)


def test_the_summary_fits_six_to_seven_printed_lines():
    lines_of = lambda text: len(text) / 100
    summary, notes = resume_writer.fit_summary(["a" * 150] * 3, ["b" * 150, "c" * 150, "d" * 150], lines_of, 6, 7)
    assert 6 <= lines_of(summary) <= 7.3 and not notes
    summary, notes = resume_writer.fit_summary(["a" * 150] * 3, [], lines_of, 6, 7)
    assert notes and "shorter summary" in notes[0]


def test_by_default_claude_or_openai_write_and_the_local_model_prints_your_lines_exactly(monkeypatch):
    from tests.conftest import FakeLLM
    monkeypatch.setattr(tailoring, "EXACT_LINES", None)                          # no JOB_AGENT_EXACT_LINES set
    local = FakeLLM()
    local.answer = {"sentences": []}
    resume, _ = _resume(local)
    records = {r.text for r in with_bank().experience_bank}
    assert all(b.text in records for b in _bullets(resume))                    # the older format: bank lines
    assert all(c["system"] != resume_writer.SYSTEM for c in local.calls)
    resume, review = _resume(FakeCloud())
    assert resume.summary_source == "ai_written" and "writing" in review


def test_the_local_model_keeps_the_earlier_letter_instructions():
    from app import cover_letter
    assert cover_letter.prompt([], cloud=False).endswith(cover_letter.TASK_LOCAL)
    assert cover_letter.prompt([], cloud=True).endswith(cover_letter.TASK)
    assert "opening_connection" in cover_letter.TASK and "opening_connection" not in cover_letter.TASK_LOCAL


class FakeCloudApp(FakeCloud):
    """Answers the letter's story call too (the app's full flow)."""

    def generate_json(self, system, user, schema, **kw):
        if "examples" in (schema.get("properties") or {}):
            self.calls.append({"system": system, "user": user})
            cards = re.findall(r"^(S\d+) — ", user, re.M)
            return {"opening": "The part of this role that interests me most is building Java API test automation.",
                    "opening_connection": None,
                    "examples": [{"id": c, "sentences": [], "connection": ""} for c in cards],
                    "closing": "I would welcome a conversation about Java API test automation for your services."}
        return super().generate_json(system, user, schema, **kw)


def test_prepare_and_each_generate_action_use_the_writer_and_keep_earlier_versions(db_path, profile_data):
    from fastapi.testclient import TestClient
    from app.api import create_app
    p = with_bank()
    with TestClient(create_app(db_path, llm=FakeCloudApp())) as c:
        c.put("/profile", json=json.loads(p.model_dump_json()))
        job_id = c.post("/jobs", json={"title": "Senior SDET", "company": "Example (fictional)",
                                       "description": SDET_JOB.description, "required_skills": ["Java"],
                                       "allow_duplicate": True}).json()["id"]
        app_id = c.post("/applications", json={"job_id": job_id}).json()["id"]
        # combined: Prepare makes both documents
        assert c.post(f"/applications/{app_id}/prepare").status_code == 202
        c.app.state.preparer.wait()
        assert c.get(f"/applications/{app_id}/prepare").json()["state"] == "done"
        docs = c.get(f"/applications/{app_id}/documents").json()
        resume = next(d for d in docs if d["kind"] == "resume")
        letter = next(d for d in docs if d["kind"] == "cover_letter")
        assert json.loads(resume["content"])["summary_source"] == "ai_written" and "writing" in resume["review"]
        assert "Thank you for your time and consideration." in letter["content"]
        # individual actions: a new version of each, the earlier version unchanged
        r2 = c.post(f"/applications/{app_id}/drafts", json={"kind": "resume"}).json()
        l2 = c.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
        assert (r2["version"], l2["version"]) == (resume["version"] + 1, letter["version"] + 1)
        again = {d["id"]: d for d in c.get(f"/applications/{app_id}/documents").json()}
        if resume["id"] in again:
            assert again[resume["id"]]["content"] == resume["content"]
        printed = c.get(f"/documents/{r2['id']}/print").text
        assert not re.search(r"\b(CH|NB|FF|TL|PR|FDE)\d{2}\b", printed)              # no record IDs on the page
