"""Entry point for the real server.

Run locally:  uvicorn app.main:app --reload       (or ./start.sh, start.command, .\\start.ps1)
Then open:    http://127.0.0.1:8000/ui/
Docs:         http://127.0.0.1:8000/docs

Importing this module creates/opens the real data folder (data/) and reads .env, which is why tests import
``create_app`` from app.api instead.

Accounts are on: everyone who opens the app signs in or creates an account, and gets their own workspace.
JOB_AGENT_ACCOUNTS=off in .env goes back to one shared workspace (optionally protected by APP_PASSWORD).
"""

import os

from app.config import LOG_DIR, get_password, load_env_file

load_env_file()

from app.api import create_app  # noqa: E402

_accounts = os.environ.get("JOB_AGENT_ACCOUNTS", "on").strip().lower() not in ("off", "0", "false", "no")
app = create_app(password=None if _accounts else get_password(), log_dir=LOG_DIR,
                 log_level=os.environ.get("LOG_LEVEL", "INFO"), accounts=_accounts,
                 local_browser=os.environ.get("JOB_AGENT_AUTO_APPLY", "on").lower() not in {"off", "0", "false"})
_port = os.environ.get("PORT", "8000")
print(f"\n  Job Application Agent is running. Open:  http://127.0.0.1:{_port}/ui/\n", flush=True)
if _accounts and (_code := app.state.accounts.setup_code()):
    print("  Data from before accounts was found. To take it over into YOUR account, sign in and open Profile ->\n"
          f"  'Bring in existing local data', then enter this setup code:  {_code}\n"
          f"  (also in {app.state.accounts.setup_code_file})\n", flush=True)
