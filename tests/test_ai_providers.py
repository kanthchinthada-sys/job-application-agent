"""Claude or OpenAI with each account's own API key, else the local model. No real API calls: the providers'
responses are faked. Keys and account names here are FICTIONAL."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import ai_settings, llm
from app.api import create_app
from tests.conftest import FakeLLM

CLAUDE_KEY = "sk-ant-fictional-test-key-0000000000001234"
OPENAI_KEY = "sk-fictional-openai-test-key-000000005678"


def test_the_answer_shape_is_made_strict_for_the_providers():
    schema = {"type": "object", "title": "Answer", "properties": {
        "text": {"type": "string", "maxLength": 300, "default": ""},
        "items": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"id": {"type": "string"}}}}},
        "required": ["text"]}
    claude = llm.strict_schema(schema)
    assert claude["additionalProperties"] is False and claude["required"] == ["text"]
    assert claude["properties"]["items"]["items"]["additionalProperties"] is False
    assert "maxLength" not in claude["properties"]["text"] and "minItems" not in claude["properties"]["items"]
    assert "title" not in claude
    openai = llm.strict_schema(schema, all_required=True)
    assert openai["required"] == ["text", "items"] and openai["properties"]["items"]["items"]["required"] == ["id"]


def test_the_newest_general_gpt_model_is_chosen_from_the_keys_own_list():
    assert llm.pick_openai_model(["gpt-4o", "gpt-5", "gpt-5.1", "gpt-5.1-mini", "gpt-realtime", "o3"]) == "gpt-5.1"
    assert llm.pick_openai_model(["gpt-4o-mini", "dall-e-3"]) is None


def _claude_response(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def test_claude_answers_in_the_asked_shape_with_effort_and_a_closed_schema():
    c = llm.ClaudeClient(CLAUDE_KEY)
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return _claude_response('{"text": "ok"}')
    c.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    assert c.generate_json("sys", "user", {"type": "object", "properties": {"text": {"type": "string"}}}) == {"text": "ok"}
    assert sent["model"] == "claude-sonnet-5-5" and sent["system"] == "sys"
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert sent["output_config"]["effort"] in ("low", "medium", "high", "xhigh", "max")
    assert "thinking" not in sent and "temperature" not in sent      # Opus 5.5 rejects both forms


def test_a_cut_off_or_declined_claude_answer_is_an_error_not_a_half_answer():
    c = llm.ClaudeClient(CLAUDE_KEY)
    for stop in ("max_tokens", "refusal"):
        c.client = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: _claude_response('{"te', stop)))
        with pytest.raises(llm.LLMOutputError):
            c.generate_json("s", "u", {"type": "object", "properties": {}})


def test_openai_answers_in_the_asked_shape():
    c = llm.OpenAIClient(OPENAI_KEY, model="gpt-5.1")
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        msg = SimpleNamespace(content='{"text": "ok"}', refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert c.generate_json("sys", "user", {"type": "object", "properties": {"text": {"type": "string"}}}) == {"text": "ok"}
    fmt = sent["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"]["required"] == ["text"] and sent["reasoning_effort"]


class _FakeProvider:
    provider = "claude"

    def __init__(self, key, model=None):
        self.key, self.model = key, model or "claude-sonnet-5-5"

    def generate_json(self, system, user, schema, **kwargs):
        return {"from": "provider"}

    def status(self):
        return {"provider": "claude", "reachable": True, "model": self.model,
                "model_available": self.key == CLAUDE_KEY, "error": None if self.key == CLAUDE_KEY else "API key not accepted"}


def test_without_a_saved_key_the_local_model_is_used_and_a_saved_key_switches_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "ClaudeClient", _FakeProvider)
    local = FakeLLM()
    account = llm.AccountLLM(tmp_path / "ws.db", local)
    assert account.current() is local
    ai_settings.save(tmp_path / "ws.db", "claude", CLAUDE_KEY, None)
    assert account.generate_json("s", "u", {}) == {"from": "provider"}
    ai_settings.save(tmp_path / "ws.db", "ollama", None, None)
    assert account.current() is local


def _sign_up(c, name):
    assert c.post("/auth/register", json={"username": name, "password": "correct horse 1"}).status_code == 200


def test_each_account_saves_its_own_key_which_is_checked_and_never_sent_back(tmp_path, monkeypatch):
    import app.llm as llm_module
    monkeypatch.setattr(llm_module, "ClaudeClient", _FakeProvider)
    app = create_app(tmp_path / "job_agent.db", llm=FakeLLM(), accounts=True)
    with TestClient(app) as a, TestClient(app) as b:
        _sign_up(a, "alex.fictional")
        _sign_up(b, "sam.fictional")
        assert a.get("/ai/settings").json()["provider"] == "ollama"
        bad = a.put("/ai/settings", json={"provider": "claude", "api_key": "sk-ant-not-a-real-key-at-all-xx"})
        assert bad.status_code == 400 and "not accepted" in bad.json()["detail"]
        r = a.put("/ai/settings", json={"provider": "claude", "api_key": CLAUDE_KEY})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {**body, "provider": "claude", "has_key": True, "key_hint": "…1234", "model": "claude-sonnet-5-5"}
        for url in ("/ai/settings", "/ai/status"):
            assert CLAUDE_KEY not in a.get(url).text
        assert a.get("/ai/status").json()["provider"] == "claude"
        # the other account is untouched: still the local model, no key
        assert b.get("/ai/settings").json()["has_key"] is False and b.get("/ai/status").json()["provider"] != "claude"
        # keeping the saved key while changing nothing else; then back to the local model
        assert a.put("/ai/settings", json={"provider": "claude"}).status_code == 200
        assert a.put("/ai/settings", json={"provider": "ollama"}).json()["has_key"] is False


def test_a_field_named_like_a_schema_keyword_is_kept():
    # the cover letter's answer has a field called "examples" (also a JSON Schema keyword the providers reject)
    from app.cover_letter import StoryDraft
    strict = llm.strict_schema(StoryDraft.model_json_schema())
    assert "examples" in strict["properties"] and "examples" in strict["required"]
    assert "title" in llm.strict_schema({"type": "object", "properties": {"title": {"type": "string", "title": "T"}}})["properties"]


def test_options_a_client_does_not_take_are_not_passed_to_it(tmp_path):
    class Plain:
        model = "plain"

        def generate_json(self, system, user, schema):
            return {"ok": True}

        def status(self):
            return {}
    account = llm.AccountLLM(tmp_path / "ws.db", Plain())
    assert account.generate_json("s", "u", {}, timeout=5, max_tokens=10) == {"ok": True}
