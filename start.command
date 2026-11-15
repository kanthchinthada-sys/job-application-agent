#!/bin/bash
# Start the Job Application Agent: double-click this file in Finder (or run ./start.command in a terminal).
# The first run sets everything up (Python packages, the screen); later runs start in seconds.
# Stop it with Ctrl+C in the window that opens.
cd "$(dirname "$0")"

# --- Python packages (only when requirements changed) ---
if [ ! -d .venv ]; then
  echo "First run: creating the Python environment (.venv)..."
  python3 -m venv .venv || { echo "Python 3 is needed: https://www.python.org/downloads/"; exit 1; }
fi
source .venv/bin/activate
STAMP=.venv/.installed-requirements
if [ ! -f "$STAMP" ] || [ requirements-app.txt -nt "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Installing Python packages..."
  if pip install -q -r requirements.txt; then
    echo "Installing the browser used to measure printed pages (once)..."
    python -m playwright install chromium >/dev/null 2>&1 || echo "(Skipped: resumes will use the page estimate.)"
    touch "$STAMP"
  else
    echo "Package install failed: check your internet connection."
  fi
fi

# --- local AI (optional) ---
open -a Ollama 2>/dev/null || echo "Ollama not found: the app works, but AI features stay off until you install it (https://ollama.com)."

# --- the screen (built when missing or when its code changed) ---
BUILT=frontend/dist/frontend/browser/index.html
if [ ! -f "$BUILT" ] || [ -n "$(find frontend/src -newer "$BUILT" -type f -print -quit)" ]; then
  if command -v npm >/dev/null; then
    [ -d frontend/node_modules ] || (cd frontend && echo "Installing the screen's packages..." && npm ci --no-audit --no-fund)
    echo "Building the screen (first time, or the screen's code changed)..."
    (cd frontend && npm run build) || echo "Build failed: the classic screen at http://127.0.0.1:8000 still works."
  else
    echo "Node.js not found (https://nodejs.org): the classic screen at http://127.0.0.1:8000 still works."
  fi
fi

( sleep 3; open "http://127.0.0.1:8000/ui/" ) &
echo "Starting the app at http://127.0.0.1:8000/ui/  (press Ctrl+C to stop)"
uvicorn app.main:app --reload
