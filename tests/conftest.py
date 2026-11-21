"""Shared test fixtures.

Every test gets a brand-new SQLite file inside pytest's ``tmp_path`` directory,
so tests are isolated from each other and never touch data/job_agent.db.
"""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


class FakeLLM:
    """Stands in for Ollama so tests are fast, free and repeatable.

    Set ``answer`` to the dict the "model" should return, or ``error`` to an
    exception it should raise. Every call is recorded in ``calls``.
    """

    model = "fake-model"

    def __init__(self):
        self.answer = {"required_skills": [], "preferred_skills": []}
        self.queue = []  # optional: answers returned in order, before falling back to `answer`
        self.error = None
        self.calls = []

    def generate_json(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self.error:
            raise self.error
        return self.queue.pop(0) if self.queue else self.answer

    def status(self):
        return {"provider": "fake", "reachable": True, "model": self.model, "model_available": True}


@pytest.fixture(autouse=True)
def no_browser_page_measuring(monkeypatch):
    """Unit tests use the page estimate only; the browser measurement has its own e2e test."""
    monkeypatch.setenv("JOB_AGENT_PAGE_MEASURE", "off")


@pytest.fixture(autouse=True)
def no_web_pages(monkeypatch):
    """Tests never read real job pages: an app built without a fake page fetcher gets one that is 'offline'."""
    from app import page_reader

    def offline(url):
        raise ConnectionError("no network in tests")
    monkeypatch.setattr(page_reader, "http_get_page", offline)


@pytest.fixture(autouse=True)
def no_min_letter_length(monkeypatch):
    """Most tests use tiny fake letters; the length rule has its own test."""
    from app import drafting
    monkeypatch.setattr(drafting, "MIN_LETTER_WORDS", 0)


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def client(db_path, fake_llm):
    with TestClient(create_app(db_path, llm=fake_llm)) as c:
        yield c


@pytest.fixture
def profile_data():
    return json.loads((EXAMPLES / "fictional_profile.json").read_text())


@pytest.fixture
def job_data():
    return json.loads((EXAMPLES / "fictional_job.json").read_text())


@pytest.fixture
def make_job(client, job_data):
    """Create a job; each call gets a unique source_url unless overridden."""
    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1
        data = copy.deepcopy(job_data)
        data["source_url"] = f"https://jobs.example.com/test/{counter['n']}"
        data["allow_duplicate"] = True   # several copies of the same fictional job
        data.update(overrides)
        response = client.post("/jobs", json=data)
        assert response.status_code == 201, response.text
        return response.json()

    return _make
