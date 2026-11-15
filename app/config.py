"""Application settings, read from environment variables.

Put local settings in a ``.env`` file in the project folder (git-ignored); see ``.env.example``.

* JOB_AGENT_DB_PATH  where the SQLite database lives (default data/job_agent.db)
* APP_PASSWORD       turns on password protection (default: off)
* LOG_LEVEL          INFO (default) or DEBUG / WARNING
* OLLAMA_URL, OLLAMA_MODEL   see app/llm.py
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "job_agent.db"
LOG_DIR = PROJECT_ROOT / "data" / "logs"


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env reader: KEY=value lines; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_db_path() -> Path:
    override = os.environ.get("JOB_AGENT_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def get_password() -> str | None:
    return os.environ.get("APP_PASSWORD") or None
