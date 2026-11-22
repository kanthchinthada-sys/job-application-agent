"""Accounts: each person who uses this installation gets their own private workspace.

* Sign in or create an account at /login. Passwords are hashed with scrypt (random salt per account) and never
  stored or logged; sessions are random tokens kept in data/accounts.db, so a restart doesn't sign you out.
* Every account has its own database file (data/users/<id>/job_agent.db): profile, jobs, applications, notes and
  documents. A request can only ever open the signed-in account's file, so another account's documents and
  download links are simply not there (404), whatever id is typed into the URL.
* Data from before accounts existed (data/job_agent.db) is never touched. Only the installation owner can take it
  over, once, by proving they control this computer: the server writes a one-time setup code to
  data/owner-setup-code.txt (readable only by your user account) and prints it in its console. Being the first
  account to register grants nothing. The data is copied into that account's workspace; the original file stays
  where it was, as a backup.
"""

import hashlib
import hmac
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app import database
from app.observability import logger

COOKIE = "job_agent_session"
SESSION_DAYS = 14
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
OPEN_PATHS = {"/login", "/auth/login", "/auth/register", "/health", "/health/live"}
USERNAME = re.compile(r"^[A-Za-z0-9._@+-]{3,60}$")
MIN_PASSWORD = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,              -- scrypt$<salt hex>$<hash hex>
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,             -- sha256 of the cookie value: a stolen database can't sign in
    user_id     INTEGER NOT NULL REFERENCES users(id),
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _check_password(password: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(_hash_password(password, bytes.fromhex(salt)), stored)
    except ValueError:
        return False


class User(BaseModel):
    id: int
    username: str


class AccountStore:
    """Users, sessions and each user's workspace file."""

    def __init__(self, root: Path, legacy_db: Path | None = None):
        self.root = Path(root)
        self.path = self.root / "accounts.db"
        self.legacy_db = legacy_db
        self._ready: set[int] = set()
        self._lock = threading.Lock()
        self.failures: dict[str, tuple[int, float]] = {}      # username -> (failures in a row, locked until)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # --- users and sessions -----------------------------------------------------------------------------
    def create_user(self, username: str, password: str) -> User:
        username = username.strip()
        if not USERNAME.match(username):
            raise ValueError("Choose a username of 3-60 letters, digits or . _ @ + - (an email address works).")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
        try:
            with self._conn() as c:
                cur = c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                                (username, _hash_password(password), _now()))
                return User(id=cur.lastrowid, username=username)
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username is taken. Sign in instead, or choose another one.") from exc

    def authenticate(self, username: str, password: str) -> User | None:
        with self._conn() as c:
            row = c.execute("SELECT id, username, password_hash FROM users WHERE username = ?",
                            (username.strip(),)).fetchone()
        if row is None:
            _hash_password(password)                 # same work either way: no hint whether the name exists
            return None
        return User(id=row["id"], username=row["username"]) if _check_password(password, row["password_hash"]) else None

    def new_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            c.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                      (hashlib.sha256(token.encode()).hexdigest(), user.id, time.time() + SESSION_DAYS * 86400))
        return token

    def user_for(self, token: str | None) -> User | None:
        if not token:
            return None
        with self._conn() as c:
            row = c.execute("SELECT u.id, u.username, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
                            "WHERE s.token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row["expires_at"] < time.time():
            return None
        return User(id=row["id"], username=row["username"])

    def end_session(self, token: str | None) -> None:
        if token:
            with self._conn() as c:
                c.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    # --- workspaces --------------------------------------------------------------------------------------
    def db_path(self, user: User) -> Path:
        path = self.root / "users" / str(user.id) / "job_agent.db"
        if user.id not in self._ready:
            with self._lock:
                database.init_db(path)
                self._ready.add(user.id)
        return path

    def _meta(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # --- the installation owner's setup code -------------------------------------------------------------
    @property
    def setup_code_file(self) -> Path:
        return self.root / "owner-setup-code.txt"

    def setup_code(self) -> str | None:
        """The one-time code that proves you own this installation (made when there is local data to take over).
        Written to data/owner-setup-code.txt with owner-only permissions; None when nothing is left to claim."""
        if not self.legacy_available():
            self.setup_code_file.unlink(missing_ok=True)
            return None
        if self.setup_code_file.exists():
            return self.setup_code_file.read_text().strip()
        code = "-".join(secrets.token_hex(3).upper() for _ in range(3))
        self.setup_code_file.write_text(code + "\n")
        try:
            self.setup_code_file.chmod(0o600)
        except OSError:
            pass
        return code

    def check_setup_code(self, attempt: str) -> bool:
        code = self.setup_code()
        norm = lambda x: re.sub(r"[^A-Z0-9]", "", (x or "").upper())
        return bool(code) and hmac.compare_digest(norm(attempt), norm(code))

    def legacy_available(self, user: User | None = None) -> bool:
        """Is there data from before accounts that nobody has taken over yet? (Taking it needs the setup code.)"""
        if self.legacy_db is None or not self.legacy_db.exists() or self._meta("legacy_claimed_by"):
            return False
        try:
            conn = sqlite3.connect(f"file:{self.legacy_db}?mode=ro", uri=True)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"profile", "jobs"} <= tables:
                    return False
                return bool(conn.execute("SELECT (SELECT COUNT(*) FROM profile) + (SELECT COUNT(*) FROM jobs)")
                            .fetchone()[0])
            finally:
                conn.close()
        except sqlite3.Error:
            return False

    def legacy_summary(self) -> dict:
        conn = sqlite3.connect(f"file:{self.legacy_db}?mode=ro", uri=True)
        try:
            count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            return {"jobs": count("jobs"), "applications": count("applications"), "documents": count("documents"),
                    "has_profile": bool(count("profile"))}
        finally:
            conn.close()

    def claim_legacy(self, user: User, setup_code: str) -> dict:
        """Copy the pre-accounts data into this account's workspace: only with the owner's setup code, only into
        an empty workspace, only once."""
        with self._lock:
            if not self.legacy_available(user):
                raise ValueError("There is no local data left to take over.")
            if not self.check_setup_code(setup_code):
                raise PermissionError("That setup code is not right. The installation owner finds it in "
                                      "data/owner-setup-code.txt or in the console where the app was started.")
            target = self.db_path(user)
            conn = database.connect(target)
            try:
                used = conn.execute("SELECT (SELECT COUNT(*) FROM profile) + (SELECT COUNT(*) FROM jobs)").fetchone()[0]
            finally:
                conn.close()
            if used:
                raise ValueError("Your workspace already has a profile or jobs. Local data can only be taken over "
                                 "into an empty workspace, so nothing is mixed or overwritten.")
            summary = self.legacy_summary()
            src = sqlite3.connect(f"file:{self.legacy_db}?mode=ro", uri=True)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)                       # consistent copy, even while the old file is open
            finally:
                src.close()
                dst.close()
            database.init_db(target)                  # any newer tables
            with self._conn() as c:
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_claimed_by', ?)", (str(user.id),))
            self.setup_code_file.unlink(missing_ok=True)          # one use only
            logger.info("local data taken over by account %s", user.id)
            return summary


class Credentials(BaseModel):
    username: str
    password: str


class ClaimIn(BaseModel):
    setup_code: str = ""


LOGIN_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Job Application Agent</title>
<style>
:root{--bg:#f3f4f6;--panel:#fff;--text:#1f2937;--muted:#6b7280;--line:#e5e7eb;--blue:#2457c5}
*{box-sizing:border-box}body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
main{background:var(--panel);padding:28px;border-radius:12px;border:1px solid var(--line);width:100%;max-width:380px}
h1{font-size:19px;margin:0 0 4px}p{color:var(--muted);font-size:14px;margin:0 0 18px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:16px}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;padding:8px 10px;font-size:15px;cursor:pointer;color:var(--muted)}
.tabs button[aria-selected=true]{border-color:var(--blue);color:var(--text);font-weight:600}
label{display:block;font-size:14px;margin-top:12px}input{width:100%;padding:9px;font-size:15px;margin-top:4px;
border:1px solid #cbd5e1;border-radius:6px}.go{width:100%;margin-top:18px;padding:10px;font-size:15px;background:var(--blue);
color:#fff;border:0;border-radius:6px;cursor:pointer}.go:disabled{opacity:.6}
#msg{color:#b00020;min-height:20px;margin-top:12px;font-size:14px}.hint{font-size:13px;color:var(--muted);margin-top:6px}
</style></head><body><main>
<h1>Job Application Agent</h1><p>Your profile, jobs and documents stay on this computer, in your own workspace.</p>
<div class="tabs" role="tablist">
  <button id="t-in" role="tab" aria-selected="true" type="button">Sign in</button>
  <button id="t-up" role="tab" aria-selected="false" type="button">Create account</button>
</div>
<form id="f">
  <label for="u">Username or email</label><input id="u" autocomplete="username" required autofocus>
  <label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" required>
  <div class="hint" id="pwhint" hidden>At least 8 characters.</div>
  <button class="go" id="go" type="submit">Sign in</button>
  <div id="msg" role="alert"></div>
</form>
<script>
let mode = "in";
const $ = id => document.getElementById(id);
// a new sign-in starts clean: nothing the previous account kept in this browser tab survives
try { Object.keys(sessionStorage).filter(k => k.startsWith("job-agent.")).forEach(k => sessionStorage.removeItem(k)); } catch (e) {}
function setMode(m) {
  mode = m;
  $("t-in").setAttribute("aria-selected", m === "in"); $("t-up").setAttribute("aria-selected", m === "up");
  $("go").textContent = m === "in" ? "Sign in" : "Create account";
  $("pw").autocomplete = m === "in" ? "current-password" : "new-password";
  $("pwhint").hidden = m === "in"; $("msg").textContent = "";
}
$("t-in").onclick = () => setMode("in"); $("t-up").onclick = () => setMode("up");
if (location.hash === "#create") setMode("up");
$("f").onsubmit = async e => {
  e.preventDefault();
  $("go").disabled = true;
  const r = await fetch(mode === "in" ? "/auth/login" : "/auth/register", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username: $("u").value, password: $("pw").value})}).catch(() => null);
  $("go").disabled = false;
  if (r && r.ok) {
    const d = await r.json();
    const next = new URLSearchParams(location.search).get("next") || "/ui/";
    // only paths on this site, never another website
    location.href = d.new_account ? "/ui/#/welcome" : (/^\/(?![\/\\])/.test(next) ? next : "/ui/");
    return;
  }
  const d = r ? await r.json().catch(() => ({})) : {detail: "Cannot reach the app server. Is it running?"};
  $("msg").textContent = d.detail || "That didn't work.";
};
</script></main></body></html>"""


def install_accounts(app: FastAPI, store: AccountStore) -> None:
    app.state.accounts = store
    router = APIRouter(tags=["accounts"])

    def _cookie(response, token: str):
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=SESSION_DAYS * 86400, path="/")

    @router.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if store.user_for(request.cookies.get(COOKIE)):
            return RedirectResponse("/ui/", status_code=303)
        return HTMLResponse(LOGIN_PAGE)

    @router.post("/auth/register")
    def register(body: Credentials):
        try:
            user = store.create_user(body.username, body.password)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})
        logger.info("account created (id %s)", user.id)
        response = JSONResponse({"username": user.username, "new_account": True})
        _cookie(response, store.new_session(user))
        return response

    @router.post("/auth/login")
    def login(body: Credentials):
        key = body.username.strip().lower()
        count, locked = store.failures.get(key, (0, 0.0))
        if time.time() < locked:
            return JSONResponse(status_code=429, content={
                "detail": f"Too many wrong passwords. Try again in {int(locked - time.time()) + 1} seconds."})
        user = store.authenticate(body.username, body.password)
        if user is None:
            count += 1
            store.failures[key] = (0, time.time() + LOCKOUT_SECONDS) if count >= MAX_FAILURES else (count, 0.0)
            logger.warning("sign-in failed")
            return JSONResponse(status_code=401, content={"detail": "Wrong username or password."})
        store.failures.pop(key, None)
        response = JSONResponse({"username": user.username, "new_account": False})
        _cookie(response, store.new_session(user))
        return response

    def _logout(request: Request):
        store.end_session(request.cookies.get(COOKIE))
        response = JSONResponse({"detail": "Signed out."})
        response.delete_cookie(COOKIE, path="/")
        return response

    router.add_api_route("/auth/logout", _logout, methods=["POST"])
    router.add_api_route("/logout", _logout, methods=["POST"], include_in_schema=False)

    @router.get("/auth/me")
    def me(request: Request):
        """The signed-in account. Whether local data exists is said, but never what is in it (no counts, no
        names): only the owner, with the setup code, can see and take it."""
        user: User = request.state.user
        return {"username": user.username, "accounts": True, "local_data_available": store.legacy_available(user)}

    claim_failures: dict[int, tuple[int, float]] = {}

    @router.post("/auth/claim-local-data")
    def claim(request: Request, body: ClaimIn | None = None):
        user: User = request.state.user
        count, locked = claim_failures.get(user.id, (0, 0.0))
        if time.time() < locked:
            return JSONResponse(status_code=429, content={"detail": "Too many wrong codes. Try again in a minute."})
        try:
            summary = store.claim_legacy(user, body.setup_code if body else "")
        except PermissionError as exc:
            count += 1
            claim_failures[user.id] = (0, time.time() + LOCKOUT_SECONDS) if count >= MAX_FAILURES else (count, 0.0)
            logger.warning("local data claim: wrong setup code")
            return JSONResponse(status_code=403, content={"detail": str(exc)})
        except ValueError as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        claim_failures.pop(user.id, None)
        return {"detail": "Your existing local data is now in your workspace.", **summary}

    app.include_router(router)

    @app.middleware("http")
    async def require_account(request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS:
            return await call_next(request)
        user = store.user_for(request.cookies.get(COOKIE))
        if user is None:
            if request.method == "GET" and (path in ("/", "/ui", "/ui/") or
                                            "text/html" in request.headers.get("accept", "")):
                return RedirectResponse("/login", status_code=303)
            return JSONResponse(status_code=401, content={"detail": "Sign in first."})
        request.state.user = user
        request.state.db_path = store.db_path(user)
        return await call_next(request)
