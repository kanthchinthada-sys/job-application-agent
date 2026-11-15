"""Entry point for the real server.

Run locally:  uvicorn app.main:app --reload
Docs:         http://127.0.0.1:8000/docs

Importing this module creates/opens the real database (data/job_agent.db) and reads .env,
which is why tests import ``create_app`` from app.api instead.
"""

import os

from app.api import create_app
from app.config import LOG_DIR, get_password, load_env_file

load_env_file()
app = create_app(password=get_password(), log_dir=LOG_DIR, log_level=os.environ.get("LOG_LEVEL", "INFO"))
