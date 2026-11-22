"""FastAPI application factory.

main.py calls this for the real server; tests call it with a temporary path.

``create_app(db_path)`` builds a fresh app bound to one database file. The
real server uses the default path; each test passes a temporary path, so tests
never touch your real data.

"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import sqlite3

from app import auth, database, observability
from app.config import PROJECT_ROOT, get_db_path
from app.errors import ConflictError, NotFoundError
from app.llm import LLMClient, LLMOutputError, LLMUnavailableError, OllamaClient
from app import importers
from app.routers import ai, applications, documents, importing, jobs, profile, search, submission

DESCRIPTION = """
Personal job application agent. AI features use a free local Ollama model.

Typical flow: `PUT /profile` → `POST /jobs` → `GET /jobs/{id}/match` → `POST /applications` → `PATCH /applications/{id}`.

The skill comparison is a **basic keyword comparison**, not an AI assessment.
"""


VERSION = "0.6.0"
UI_DIR = PROJECT_ROOT / "frontend" / "dist" / "frontend" / "browser"   # output of: cd frontend && npm run build


def create_app(db_path: Path | None = None, llm: LLMClient | None = None, fetch=None,
               password: str | None = None, log_dir: Path | None = None, log_level: str = "INFO",
               ui_dir: Path | None = None, fetch_page=None, request_api=None, accounts: bool = False) -> FastAPI:
    """``password``: turn on sign-in (main.py passes APP_PASSWORD). ``log_dir``: also log to a file.
    ``ui_dir``: the built Angular screen (default frontend/dist/frontend/browser), served at /ui/ if it exists."""
    db_path = Path(db_path) if db_path else get_db_path()
    observability.setup_logging(log_dir, log_level)
    database.init_db(db_path)

    app = FastAPI(title="Job Application Agent", version=VERSION, description=DESCRIPTION)
    app.state.db_path = db_path
    app.state.llm = llm or OllamaClient.from_env()  # tests pass a fake client
    app.state.fetch = fetch or importers.http_fetch  # tests pass a fake fetcher (no network)
    from app import page_reader
    app.state.fetch_page = fetch_page or page_reader.http_get_page   # job web pages (HTML); faked in tests
    app.state.request_api = request_api                # keyed job-search APIs (JSearch, Jooble); faked in tests
    from app.preparation import Preparer
    app.state.preparer = Preparer(db_path, app.state.llm)   # resume + cover letter in the background
    app.state.preparers = {}                                # accounts on: one per workspace (dependencies.preparer)
    from app.intake_runs import IntakeWorker
    app.state.intake = IntakeWorker(db_path, app.state.llm)  # the model's step after adding a job, in the background
    app.state.intake_workers = {}
    app.state.accounts = None

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError):
        body = {"detail": str(exc)}
        if exc.existing_id is not None:
            body["existing_id"] = exc.existing_id
        return JSONResponse(status_code=409, content=body)

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable_handler(request: Request, exc: LLMUnavailableError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LLMOutputError)
    async def llm_output_handler(request: Request, exc: LLMOutputError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def home():
        """The browser screen: http://127.0.0.1:8000 (with accounts: the full app at /ui/)"""
        if accounts:
            return RedirectResponse("/ui/", status_code=303)
        return FileResponse(PROJECT_ROOT / "app" / "static" / "index.html")

    if not accounts:
        @app.get("/auth/me", tags=["accounts"])
        def me():
            """Single-user mode: no accounts."""
            return {"username": None, "accounts": False, "local_data_available": False}

    @app.get("/static/fictional_profile.json", include_in_schema=False)
    def example_profile():
        return FileResponse(PROJECT_ROOT / "examples" / "fictional_profile.json")

    @app.get("/health/live", tags=["meta"])
    def live():
        """Is the server process up? (used by Docker's health check)"""
        return {"status": "ok"}

    @app.get("/health", tags=["meta"])
    def health():
        """Can the app do its job? Database is required; the AI is optional (status 'degraded' without it)."""
        checks = {}
        try:
            conn = database.connect(app.state.db_path)
            try:
                jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            finally:
                conn.close()
            checks["database"] = {"ok": True} if accounts else {"ok": True, "jobs": jobs}
        except sqlite3.Error as exc:
            observability.logger.error("health: database check failed: %s", exc)
            checks["database"] = {"ok": False, "error": "database not readable"}
        ai = app.state.llm.status()
        checks["ai"] = {"ok": bool(ai.get("reachable") and ai.get("model_available")), "model": ai.get("model"),
                        "reachable": ai.get("reachable")}
        if not checks["database"]["ok"]:
            status, code = "down", 503
        elif not checks["ai"]["ok"]:
            status, code = "degraded", 200
        else:
            status, code = "ok", 200
        body = {"status": status, "version": VERSION, "password_protection": app.state.auth is not None,
                "accounts": accounts,
                "angular_screen": app.state.ui_available,
                "checks": checks}
        return JSONResponse(status_code=code, content=body)

    ui_dir = Path(ui_dir) if ui_dir else UI_DIR
    if (ui_dir / "index.html").exists():
        @app.get("/ui", include_in_schema=False)
        def ui_root():
            return RedirectResponse("/ui/", status_code=307)

        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")
    app.state.ui_available = (ui_dir / "index.html").exists()

    app.include_router(profile.router)
    app.include_router(jobs.router)
    app.include_router(applications.router)
    app.include_router(documents.router)
    app.include_router(ai.router)
    app.include_router(importing.router)
    app.include_router(submission.router)
    app.include_router(search.router)

    # Order matters: the middleware added LAST runs FIRST, so logging wraps sign-in and sees every 401.
    if accounts:
        from app.accounts import AccountStore, install_accounts
        install_accounts(app, AccountStore(db_path.parent, legacy_db=db_path))
        app.state.auth = None
    else:
        auth.install_auth(app, password)
    observability.add_request_logging(app)
    observability.logger.info("app started (db=%s, %s)", db_path.name,
                              "accounts on" if accounts else f"password protection {'on' if password else 'off'}")
    return app
