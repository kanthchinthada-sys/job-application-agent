"""AI endpoints, tested with a fake model (no Ollama needed)."""

import httpx
import pytest

from app.extraction import found_in_text
from app.llm import LLMOutputError, LLMUnavailableError, OllamaClient

DESCRIPTION = (
    "FICTIONAL posting. Requirements: 3+ years of Python, experience with REST APIs and PostgreSQL. "
    "Nice to have: Kubernetes, TypeScript."
)


def test_extract_splits_found_and_unsupported_skills(client, fake_llm):
    fake_llm.answer = {
        "required_skills": ["Python", "rest apis", "PostgreSQL", "Java"],  # Java is not in the text
        "preferred_skills": ["Kubernetes", "TypeScript", "python"],        # duplicate of a required skill
    }
    response = client.post("/ai/extract-skills", json={"description": DESCRIPTION})
    assert response.status_code == 200
    body = response.json()
    assert body["required_skills"] == ["Python", "rest apis", "PostgreSQL"]
    assert body["preferred_skills"] == ["Kubernetes", "TypeScript"]
    assert body["needs_review"] == ["Java"]
    assert body["method"] == "llm_extraction_v1"
    assert body["model"] == "fake-model"


def test_extract_sends_description_and_schema_to_model(client, fake_llm):
    client.post("/ai/extract-skills", json={"description": DESCRIPTION})
    call = fake_llm.calls[0]
    assert DESCRIPTION in call["user"]
    assert "Never add skills" in call["system"]
    assert set(call["schema"]["properties"]) == {"required_skills", "preferred_skills"}


def test_extract_cleans_blank_and_duplicate_items(client, fake_llm):
    fake_llm.answer = {"required_skills": ["  Python. ", "", "PYTHON"], "preferred_skills": []}
    body = client.post("/ai/extract-skills", json={"description": DESCRIPTION}).json()
    assert body["required_skills"] == ["Python"]


def test_extract_does_not_save_anything(client, fake_llm):
    fake_llm.answer = {"required_skills": ["Python"], "preferred_skills": []}
    client.post("/ai/extract-skills", json={"description": DESCRIPTION})
    assert client.get("/jobs").json() == []


def test_extract_rejects_short_description(client, fake_llm):
    assert client.post("/ai/extract-skills", json={"description": "too short"}).status_code == 422
    assert fake_llm.calls == []


def test_ollama_unavailable_returns_503(client, fake_llm):
    fake_llm.error = LLMUnavailableError("Cannot reach Ollama")
    response = client.post("/ai/extract-skills", json={"description": DESCRIPTION})
    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_malformed_model_output_returns_502(client, fake_llm):
    fake_llm.answer = {"required_skills": "Python, SQL"}  # string instead of a list
    response = client.post("/ai/extract-skills", json={"description": DESCRIPTION})
    assert response.status_code == 502


def test_status_endpoint(client):
    assert client.get("/ai/status").json()["reachable"] is True


@pytest.mark.parametrize(
    "skill, expected",
    [("Python", True), ("postgres", True), ("Docker", False), ("PostgreSQL", True), ("Java", False), ("REST APIs", True)],
)
def test_found_in_text(skill, expected):
    assert found_in_text(skill, DESCRIPTION) is expected


def test_real_client_reports_when_ollama_is_down():
    # Port 9 is the "discard" port: nothing listens there, so the connection fails fast.
    client = OllamaClient(base_url="http://127.0.0.1:9", timeout=2)
    with pytest.raises(LLMUnavailableError):
        client.generate_json("s", "u", {})
    assert client.status()["reachable"] is False


def test_real_client_parses_ollama_response(monkeypatch):
    def fake_post(url, json, timeout):
        assert json["format"] == {"type": "object"} and json["think"] is False
        return httpx.Response(200, json={"message": {"content": '{"required_skills": ["Go"]}'}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert OllamaClient().generate_json("s", "u", {"type": "object"}) == {"required_skills": ["Go"]}


def test_real_client_raises_on_non_json(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"message": {"content": "hi"}}))
    with pytest.raises(LLMOutputError):
        OllamaClient().generate_json("s", "u", {})
