"""Each workspace's AI provider: Claude or OpenAI with the account's own API key, or the local Ollama model.

The key stays on this server, in the account's own workspace database; it is never sent back to the browser
(only its last four characters, so you can tell which key is saved)."""
from datetime import datetime, timezone
from pathlib import Path

from app import database

PROVIDERS = ("claude", "openai", "ollama")

_TABLE = """CREATE TABLE IF NOT EXISTS ai_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1), provider TEXT NOT NULL, api_key TEXT, model TEXT, updated_at TEXT NOT NULL)"""


def load(db_path: Path) -> dict | None:
    conn = database.connect(Path(db_path))
    try:
        conn.execute(_TABLE)
        row = conn.execute("SELECT provider, api_key, model, updated_at FROM ai_settings WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save(db_path: Path, provider: str, api_key: str | None, model: str | None) -> None:
    conn = database.connect(Path(db_path))
    try:
        conn.execute(_TABLE)
        conn.execute("INSERT INTO ai_settings (id, provider, api_key, model, updated_at) VALUES (1, ?, ?, ?, ?) "
                     "ON CONFLICT(id) DO UPDATE SET provider = excluded.provider, api_key = excluded.api_key, "
                     "model = excluded.model, updated_at = excluded.updated_at",
                     (provider, api_key, model, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def public(settings: dict | None) -> dict:
    """What the browser may see: the provider, the model, and whether a key is saved (its last 4 characters)."""
    if not settings:
        return {"provider": "ollama", "model": None, "has_key": False, "key_hint": None, "updated_at": None}
    key = settings.get("api_key") or ""
    return {"provider": settings["provider"], "model": settings.get("model"), "has_key": bool(key),
            "key_hint": ("…" + key[-4:]) if len(key) >= 8 else None, "updated_at": settings.get("updated_at")}
