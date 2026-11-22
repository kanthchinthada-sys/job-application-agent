# Start the Job Application Agent on Windows (PowerShell):   .\start.ps1
# Prerequisites: Python 3.12+, Node.js 20+ (to build the screen once), and Ollama (https://ollama.com) for AI features.
# If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Port = if ($env:PORT) { $env:PORT } else { "8000" }

if (-not (Test-Path .venv)) {
  Write-Host "First run: creating the Python environment (.venv)..."
  py -3 -m venv .venv
}
$py = ".venv\Scripts\python.exe"
$stamp = ".venv\.installed-requirements"
if (-not (Test-Path $stamp) -or (Get-Item requirements-app.txt).LastWriteTime -gt (Get-Item $stamp -ErrorAction SilentlyContinue).LastWriteTime) {
  Write-Host "Installing Python packages..."
  & $py -m pip install -q -r requirements.txt
  try { & $py -m playwright install chromium | Out-Null } catch { Write-Host "(Chromium skipped: resumes use the page estimate.)" }
  New-Item -ItemType File -Force $stamp | Out-Null
}

if (-not (Test-Path frontend\dist\frontend\browser\index.html)) {
  Write-Host "Building the screen..."
  Push-Location frontend
  if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund }
  npm run build | Out-Null
  Pop-Location
}

try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags | Out-Null }
catch { Write-Host "Ollama isn't running: the app works, AI features stay off until you start the Ollama app." }

Write-Host ""
Write-Host "  Job Application Agent:  http://127.0.0.1:$Port/ui/   (Ctrl+C to stop)"
Write-Host ""
$env:PORT = $Port
& $py -m uvicorn app.main:app --host 127.0.0.1 --port $Port
