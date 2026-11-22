#!/bin/bash
# Start the Job Application Agent on macOS or Linux:   ./start.sh
# Prerequisites: Python 3.12+, Node.js 20+ (to build the screen once), and Ollama (https://ollama.com) for AI features.
# The first run installs everything; later runs start in seconds. Stop with Ctrl+C.
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

if [ ! -d .venv ]; then
  echo "First run: creating the Python environment (.venv)..."
  python3 -m venv .venv || { echo "Python 3.12+ is needed: https://www.python.org/downloads/"; exit 1; }
fi
source .venv/bin/activate
STAMP=.venv/.installed-requirements
if [ ! -f "$STAMP" ] || [ requirements-app.txt -nt "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Installing Python packages..."
  pip install -q -r requirements.txt
  python -m playwright install chromium >/dev/null 2>&1 || echo "(Chromium skipped: resumes use the page estimate.)"
  touch "$STAMP"
fi

BUILT=frontend/dist/frontend/browser/index.html
if [ ! -f "$BUILT" ] || [ -n "$(find frontend/src -newer "$BUILT" -type f -print -quit)" ]; then
  command -v npm >/dev/null || { echo "Node.js is needed once to build the screen: https://nodejs.org"; exit 1; }
  [ -d frontend/node_modules ] || (cd frontend && npm ci --no-audit --no-fund)
  echo "Building the screen..."
  (cd frontend && npm run build >/dev/null)
fi

if ! curl -s -o /dev/null "http://127.0.0.1:11434/api/tags"; then
  (command -v open >/dev/null && open -a Ollama 2>/dev/null) || (command -v ollama >/dev/null && (ollama serve >/dev/null 2>&1 &)) \
    || echo "Ollama isn't running: the app works, AI features stay off until you start it (https://ollama.com)."
fi

echo ""
echo "  Job Application Agent:  http://127.0.0.1:${PORT}/ui/   (Ctrl+C to stop)"
echo ""
PORT="$PORT" exec uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
