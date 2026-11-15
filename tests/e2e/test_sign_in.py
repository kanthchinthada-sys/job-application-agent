"""Password protection in a real browser: redirected to sign in, wrong password refused, sign in, sign out."""

import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect

from app.api import create_app
from tests.e2e.conftest import E2EFakeLLM, _free_port, fake_fetch

pytestmark = pytest.mark.e2e


@pytest.fixture
def locked_server(tmp_path):
    app = create_app(tmp_path / "e2e.db", llm=E2EFakeLLM(), fetch=fake_fetch, password="fictional-pass")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def test_sign_in_and_out(browser, locked_server):
    ctx = browser.new_context(base_url=locked_server)
    page = ctx.new_page()
    page.goto("/")
    expect(page).to_have_url(f"{locked_server}/login")                    # sent to sign in first

    page.get_by_label("Password").fill("wrong")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_have_text("Wrong password.")

    page.get_by_label("Password").fill("fictional-pass")
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(f"{locked_server}/")
    expect(page.get_by_role("button", name="Jobs")).to_be_visible()

    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(f"{locked_server}/login")
    page.goto("/")
    expect(page).to_have_url(f"{locked_server}/login")                    # really signed out
    ctx.close()
