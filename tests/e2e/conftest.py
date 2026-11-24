"""End-to-end browser tests (Playwright).

Each test session starts the real app in a background thread on a free port, with:
  * a temporary SQLite database (your real data is never touched),
  * a fake AI (deterministic answers, no Ollama needed),
  * fake job-board responses (no network).
Then a real Chromium browser clicks through the app like a user would.

Run:  pytest -m e2e            (first time: pip install playwright && python -m playwright install chromium)
Watch it click through the app:  HEADED=1 pytest -m e2e   (slowed down so you can follow)
"""

import os
import socket
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api", reason="pip install playwright && playwright install chromium")
import uvicorn  # noqa: E402

from app.api import create_app  # noqa: E402
from app.llm import LLMUnavailableError  # noqa: E402
from tests.test_importing import fake_fetch as _board_fetch  # noqa: E402
from tests.test_submission import GH_QUESTIONS  # noqa: E402


def fake_fetch(url):
    """Fake job boards: postings (test_importing), Greenhouse application questions (test_submission) and the
    Find-jobs sources (test_job_search)."""
    if url.endswith("/jobs/111?questions=true"):
        return GH_QUESTIONS
    if any(src in url for src in ("remotive", "jobicy", "arbeitnow", "adzuna")):
        from tests.test_job_search import make_fetch
        return make_fetch([])(url)
    return _board_fetch(url)


def _no_pages(url):
    """No network: job web pages can't be read in the browser tests (the app then asks you to paste)."""
    raise ConnectionError("no network in tests")


class E2EFakeLLM:
    """Answers by the JSON schema the app asks for, like a well-behaved model would."""

    model = "fake-e2e-model"

    def __init__(self):
        self.offline = False
        self.intake_gate = None        # a threading.Event: reading a job posting waits for it (a slow model)
        self.intake_answer = None

    def generate_json(self, system, user, schema):
        if self.offline:
            raise LLMUnavailableError("Cannot reach Ollama at http://localhost:11434. Is the Ollama app running?")
        props = schema.get("properties", {})
        if "company" in props and self.intake_gate is not None:
            self.intake_gate.wait(20)
            return self.intake_answer or {}
        if "bullets" in props:
            return {"bullets": []}
        if "sentences" in props:
            return {"sentences": []}
        if "fixes" in props:          # the cover letter's repair: rewrite the flagged sentence from a real fact
            return {"fixes": [{"id": "S1", "text": "I worked with customer support to reproduce production issues "
                                                   "using Docker containers.", "fact_ids": ["F5"]}], "additions": []}
        if {"text", "examples"} & props.keys():
            if "PROBLEMS FOUND" in user:
                return {"text": "I build pytest suites for a payments API used by three internal teams. When a run "
                                "fails I work out whether it is the product, the test or the environment.",
                        "fact_ids_used": ["F4"]}
            return {"text": "I am thrilled to apply. I build pytest suites for a payments API.", "fact_ids_used": ["F4"]}
        return {"required_skills": ["Python", "Playwright"], "preferred_skills": []}

    def status(self):
        return {"provider": "fake", "reachable": not self.offline, "model": self.model, "model_available": True}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_llm_e2e():
    return E2EFakeLLM()


@pytest.fixture
def server(tmp_path, fake_llm_e2e):
    """A fresh app per test: no state leaks between tests."""
    app = create_app(tmp_path / "e2e.db", llm=fake_llm_e2e, fetch=fake_fetch, fetch_page=_no_pages)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with playwright_sync.sync_playwright() as p:
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE lets CI or a sandbox use an existing Chrome.
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        headed = os.environ.get("HEADED") == "1"
        options = {"headless": not headed, "slow_mo": 350 if headed else 0}
        b = p.chromium.launch(executable_path=exe, **options) if exe else p.chromium.launch(**options)
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={"width": 1280, "height": 1000}, base_url=server)
    page = context.new_page()
    # A visible (HEADED=1) Chrome window first loads about:blank; if that lands after our first navigation it
    # interrupts it. Load the app once here, retrying if the start-up blank page gets in the way.
    page.wait_for_load_state()
    for attempt in range(3):
        try:
            page.goto("/")
            break
        except playwright_sync.Error as exc:
            if "interrupted by another navigation" not in str(exc) or attempt == 2:
                raise
            page.wait_for_load_state()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))   # any JavaScript error fails the test
    yield page
    context.close()
    assert errors == [], f"JavaScript errors on the page: {errors}"
