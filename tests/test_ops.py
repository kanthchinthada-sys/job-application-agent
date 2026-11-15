"""Milestone 5: health checks, logging (with redaction), security headers and password protection."""

import logging

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.api import create_app
from app.config import load_env_file
from app.observability import redact


# ---------- health ----------

def test_health_ok_with_database_and_ai(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == {"ok": True, "jobs": 0}
    assert body["checks"]["ai"]["ok"] is True
    assert body["password_protection"] is False


def test_health_degraded_when_ai_is_off(client, fake_llm, monkeypatch):
    monkeypatch.setattr(fake_llm, "status", lambda: {"reachable": False, "model": "qwen3:8b", "model_available": False})
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "degraded"       # the app still works without AI


def test_health_down_when_database_is_unreadable(client, db_path):
    db_path.write_bytes(b"this is not a sqlite file" * 100)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "down"
    assert "not a sqlite" not in r.text                                     # no internal details leak


def test_liveness(client):
    assert client.get("/health/live").json() == {"status": "ok"}


# ---------- logging & headers ----------

def test_redact_masks_emails_and_phones():
    line = redact("send to jordan.sample@example.com or call +1 (555) 010-0100 about job 42")
    assert "example.com" not in line and "555" not in line
    assert "[email]" in line and "[phone]" in line and "job 42" in line


def test_requests_are_logged_without_query_strings_or_personal_data(db_path, fake_llm, tmp_path):
    log_dir = tmp_path / "logs"
    with TestClient(create_app(db_path, llm=fake_llm, log_dir=log_dir)) as c:
        c.get("/jobs?search=jordan.sample@example.com")
        c.get("/jobs/999")
    for h in logging.getLogger("job_agent").handlers:
        h.flush()
    text = (log_dir / "app.log").read_text()
    assert "GET /jobs -> 200" in text and "GET /jobs/999 -> 404" in text
    assert "example.com" not in text and "search=" not in text


def test_request_id_and_security_headers(client):
    r = client.get("/jobs", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"                         # personal data is not cached


# ---------- password protection ----------

@pytest.fixture
def locked(db_path, fake_llm):
    with TestClient(create_app(db_path, llm=fake_llm, password="correct horse")) as c:
        yield c


def test_everything_needs_sign_in(locked):
    assert locked.get("/jobs").status_code == 401
    assert locked.get("/profile").status_code == 401
    assert locked.get("/openapi.json").status_code == 401
    r = locked.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert locked.get("/health").status_code == 200                         # health stays open (no personal data)
    assert locked.get("/health").json()["password_protection"] is True
    assert "Password" in locked.get("/login").text


def test_wrong_password_is_refused(locked):
    r = locked.post("/login", json={"password": "wrong"})
    assert r.status_code == 401 and auth.COOKIE not in r.cookies
    assert locked.get("/jobs").status_code == 401


def test_sign_in_and_out(locked):
    r = locked.post("/login", json={"password": "correct horse"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert locked.get("/jobs").status_code == 200
    locked.post("/logout")
    locked.cookies.clear()
    assert locked.get("/jobs").status_code == 401


def test_logout_invalidates_the_session_on_the_server(locked):
    locked.post("/login", json={"password": "correct horse"})
    token = locked.cookies.get(auth.COOKIE)
    locked.post("/logout")
    locked.cookies.set(auth.COOKIE, token)                                  # replaying the old cookie
    assert locked.get("/jobs").status_code == 401


def test_forged_cookie_is_refused(locked):
    locked.cookies.set(auth.COOKIE, "made-up-token")
    assert locked.get("/jobs").status_code == 401


def test_expired_session_is_refused(locked, monkeypatch):
    locked.post("/login", json={"password": "correct horse"})
    real_time = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real_time() + auth.SESSION_HOURS * 3600 + 1)
    assert locked.get("/jobs").status_code == 401


def test_lockout_after_repeated_wrong_passwords(locked):
    for _ in range(auth.MAX_FAILURES):
        assert locked.post("/login", json={"password": "nope"}).status_code == 401
    r = locked.post("/login", json={"password": "correct horse"})          # even the right one, during lockout
    assert r.status_code == 429


def test_password_never_reaches_the_log(db_path, fake_llm, tmp_path):
    log_dir = tmp_path / "logs"
    with TestClient(create_app(db_path, llm=fake_llm, password="s3cret-pass", log_dir=log_dir)) as c:
        c.post("/login", json={"password": "wrong-guess-xyz"})
        c.post("/login", json={"password": "s3cret-pass"})
    for h in logging.getLogger("job_agent").handlers:
        h.flush()
    text = (log_dir / "app.log").read_text()
    assert "login failed" in text and "login ok" in text
    assert "s3cret-pass" not in text and "wrong-guess-xyz" not in text


def test_password_off_by_default(client):
    assert client.get("/jobs").status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 303


# ---------- .env ----------

def test_env_file_is_read_but_real_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nAPP_PASSWORD='from-file'\nLOG_LEVEL=DEBUG\n")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    load_env_file(env)
    import os
    assert os.environ["APP_PASSWORD"] == "from-file" and os.environ["LOG_LEVEL"] == "WARNING"
    monkeypatch.delenv("APP_PASSWORD")


# ---------- Angular screen (/ui) ----------

@pytest.fixture
def built_ui(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<app-root></app-root>")
    (ui / "main.js").write_text("console.log('fictional bundle')")
    return ui


def test_angular_screen_is_served_when_built(db_path, fake_llm, built_ui):
    with TestClient(create_app(db_path, llm=fake_llm, ui_dir=built_ui)) as c:
        assert c.get("/ui", follow_redirects=False).headers["location"] == "/ui/"
        assert "<app-root>" in c.get("/ui/").text
        assert c.get("/ui/main.js").status_code == 200
        assert c.get("/health").json()["angular_screen"] is True


def test_no_angular_screen_until_it_is_built(db_path, fake_llm, tmp_path):
    with TestClient(create_app(db_path, llm=fake_llm, ui_dir=tmp_path / "missing")) as c:
        assert c.get("/ui/").status_code == 404
        assert c.get("/health").json()["angular_screen"] is False
        assert c.get("/").status_code == 200                                  # the original screen still works


def test_angular_screen_needs_sign_in_too(db_path, fake_llm, built_ui):
    with TestClient(create_app(db_path, llm=fake_llm, ui_dir=built_ui, password="pw")) as c:
        r = c.get("/ui/", headers={"accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        c.post("/login", json={"password": "pw"})
        assert "<app-root>" in c.get("/ui/").text


def test_login_page_only_returns_to_paths_on_this_site(locked):
    page = locked.get("/login").text
    assert r'/^\/(?![\/\\])/.test(next)' in page
