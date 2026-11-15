"""Humanizer: pattern detection (pure) and the rewrite pass inside drafting."""

import pytest

from app.humanizer import find_ai_patterns, report

AI_STYLE = (
    "I am writing to express my interest in this role. I am thrilled to apply, as I have a proven track record "
    "of delivering robust test automation — and I leverage cutting-edge tools to foster quality. "
    "Furthermore, my experience aligns perfectly with your needs. I look forward to the opportunity to discuss."
)
HUMAN_STYLE = (
    "Dear Hiring Team,\n\nAt Contoso Health I write the Java API tests for our imaging microservices. "
    "Most of my week goes into figuring out why a Jenkins run failed: was it the product, the test, or the "
    "cluster? That's the part of QA I like.\n\nSincerely,\nJordan"
)


def test_detects_typical_ai_patterns():
    names = {f.pattern for f in find_ai_patterns(AI_STYLE)}
    assert {"formulaic opening", "hype phrase", "AI-favourite word", "em dash", "stiff transition",
            "vague significance", "formulaic closing"} <= names
    assert report(AI_STYLE).score < 50


def test_plain_writing_scores_high():
    assert report(HUMAN_STYLE).findings == []
    assert report(HUMAN_STYLE).score == 100


@pytest.mark.parametrize("phrase", ["not only fast but also reliable", "Moreover, I led triage."])
def test_structural_patterns(phrase):
    assert find_ai_patterns(phrase)


def test_words_inside_other_words_are_not_flagged():
    assert find_ai_patterns("Landscaping company, Dynamics 365 consultant") == []


# ---- integration with drafting ----

@pytest.fixture
def app_id(client, profile_data, make_job):
    client.put("/profile", json=profile_data)
    return client.post("/applications", json={"job_id": make_job()["id"]}).json()["id"]


def test_rewrite_pass_used_when_it_is_more_human(client, fake_llm, app_id):
    fake_llm.queue = [
        {"text": AI_STYLE, "fact_ids_used": ["F1"]},
        {"text": "I build pytest suites for a payments API. I like finding why tests fail.", "fact_ids_used": ["F5"]},
    ]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    h = doc["review"]["humanizer"]
    assert h["rewritten"] is True and h["score"] == 100 and h["score_first_draft"] < 50
    assert "I build pytest suites for a payments API" in doc["content"]
    assert "PROBLEMS FOUND" in fake_llm.calls[1]["user"] and "SOUNDS AI-WRITTEN" in fake_llm.calls[1]["user"]


def test_rewrite_rejected_if_it_adds_unsupported_claims(client, fake_llm, app_id):
    fake_llm.queue = [
        {"text": "I am thrilled to apply. I build pytest suites.", "fact_ids_used": ["F5"]},
        {"text": "I build pytest suites and cut release time by 64%.", "fact_ids_used": ["F5"]},  # invented number
    ]
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    assert "I am thrilled to apply. I build pytest suites." in doc["content"]
    assert doc["review"]["humanizer"]["rewritten"] is False
    assert doc["review"]["humanizer"]["remaining"][0]["pattern"] == "formulaic opening"


def test_no_rewrite_call_when_draft_is_clean(client, fake_llm, app_id):
    fake_llm.answer = {"text": "I build pytest suites for a payments API.", "fact_ids_used": ["F5"]}
    calls_before = len(fake_llm.calls)
    doc = client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"}).json()
    assert len(fake_llm.calls) - calls_before == 1
    assert doc["review"]["humanizer"]["score"] == 100


def test_style_rules_are_in_the_prompt(client, fake_llm, app_id):
    fake_llm.answer = {"text": "Fine.", "fact_ids_used": ["F1"]}
    client.post(f"/applications/{app_id}/drafts", json={"kind": "cover_letter"})
    assert "No em dashes" in fake_llm.calls[-1]["system"]
