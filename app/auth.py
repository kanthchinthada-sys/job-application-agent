"""Optional password protection.

Off by default, so the app works out of the box on your own Mac (it only listens on 127.0.0.1).
Turn it on by putting APP_PASSWORD=... in the .env file (git-ignored).

When on:
* every page and API call needs a session cookie, except /login and /health;
* the browser is sent to /login, API calls get 401;
* the cookie is HttpOnly + SameSite=Strict, random, and expires after SESSION_HOURS;
* after MAX_FAILURES wrong passwords, logins are blocked for LOCKOUT_SECONDS;
* passwords are compared in constant time and never logged.
"""

import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.observability import logger

COOKIE = "job_agent_session"
SESSION_HOURS = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
OPEN_PATHS = {"/login", "/logout", "/health", "/health/live"}


class AuthState:
    def __init__(self, password: str):
        self._digest = hashlib.sha256(password.encode()).digest()
        self.sessions: dict[str, float] = {}      # token -> expiry time (memory only: restart = log in again)
        self.failures = 0
        self.locked_until = 0.0

    def check_password(self, attempt: str) -> bool:
        return hmac.compare_digest(hashlib.sha256(attempt.encode()).digest(), self._digest)

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + SESSION_HOURS * 3600
        return token

    def valid(self, token: str | None) -> bool:
        expiry = self.sessions.get(token or "")
        if expiry is None:
            return False
        if expiry < time.time():
            self.sessions.pop(token, None)
            return False
        return True


class LoginIn(BaseModel):
    password: str


LOGIN_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Job Application Agent</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f6f8;display:grid;place-items:center;height:100vh;margin:0}
form{background:#fff;padding:28px;border-radius:10px;box-shadow:0 2px 10px #0001;width:300px}
h1{font-size:18px;margin:0 0 16px}input,button{width:100%;box-sizing:border-box;padding:9px;font-size:15px;margin-top:8px}
button{background:#2457c5;color:#fff;border:0;border-radius:6px;cursor:pointer}#msg{color:#b00020;min-height:20px;margin-top:10px;font-size:14px}</style>
</head><body><form id="f"><h1>Job Application Agent</h1>
<label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" autofocus>
<button type="submit">Sign in</button><div id="msg" role="alert"></div></form>
<script>
document.getElementById("f").onsubmit = async e => {
  e.preventDefault();
  const r = await fetch("/login", {method: "POST", headers: {"Content-Type": "application/json"},
                                   body: JSON.stringify({password: document.getElementById("pw").value})});
  const next = new URLSearchParams(location.search).get("next") || "/";
  // after sign-in, go back where you were: only paths on this site, never another website
  if (r.ok) { location.href = /^\/(?![\/\\])/.test(next) ? next : "/"; return; }
  const d = await r.json().catch(() => ({}));
  document.getElementById("msg").textContent = d.detail || "Sign-in failed";
};
</script></body></html>"""


def install_auth(app: FastAPI, password: str | None) -> None:
    """Protect the whole app with ``password``. Does nothing when password is empty."""
    app.state.auth = AuthState(password) if password else None
    router = APIRouter(tags=["auth"])

    @router.get("/login", include_in_schema=False)
    def login_page():
        if app.state.auth is None:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(LOGIN_PAGE)

    @router.post("/login")
    def login(body: LoginIn):
        auth: AuthState | None = app.state.auth
        if auth is None:
            return {"detail": "Password protection is off."}
        now = time.time()
        if now < auth.locked_until:
            wait = int(auth.locked_until - now) + 1
            return JSONResponse(status_code=429, content={"detail": f"Too many wrong passwords. Try again in {wait} seconds."})
        if not auth.check_password(body.password):
            auth.failures += 1
            logger.warning("login failed (%s in a row)", auth.failures)
            if auth.failures >= MAX_FAILURES:
                auth.locked_until = now + LOCKOUT_SECONDS
                auth.failures = 0
            return JSONResponse(status_code=401, content={"detail": "Wrong password."})
        auth.failures = 0
        logger.info("login ok")
        response = JSONResponse({"detail": "Signed in."})
        response.set_cookie(COOKIE, auth.new_session(), httponly=True, samesite="strict",
                            max_age=SESSION_HOURS * 3600, path="/")
        return response

    @router.post("/logout")
    def logout(request: Request):
        if app.state.auth is not None:
            app.state.auth.sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = JSONResponse({"detail": "Signed out."})
        response.delete_cookie(COOKIE, path="/")
        return response

    app.include_router(router)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        auth: AuthState | None = app.state.auth
        if auth is None or request.url.path in OPEN_PATHS or auth.valid(request.cookies.get(COOKIE)):
            return await call_next(request)
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse(status_code=401, content={"detail": "Sign in first."})
