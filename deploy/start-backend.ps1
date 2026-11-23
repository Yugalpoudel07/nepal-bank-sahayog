# start-backend.ps1
# Starts the Nepal Bank Sahayak API for public use through Tailscale Funnel. Run it by hand, e.g. in a
# VS Code terminal:   powershell -ExecutionPolicy Bypass -File deploy\start-backend.ps1
# Stop it with Ctrl+C. Logs are shown in the terminal.

$ErrorActionPreference = "Stop"
$backend = (Resolve-Path (Join-Path $PSScriptRoot "..\backend")).Path

# The website's address (Cloudflare Pages) is read from deploy\allowed-origin.txt, so it can be
# changed without editing backend\.env. Values set here override the ones in .env.
$originFile = Join-Path $PSScriptRoot "allowed-origin.txt"
$origins = "http://localhost:5173"
if (Test-Path $originFile) {
    # ReadAllText returns "" for an empty file (Get-Content -Raw returns $null in Windows PowerShell 5.1)
    $site = [System.IO.File]::ReadAllText($originFile).Trim().TrimEnd("/")
    if ($site) { $origins = "$site,$origins" }
}
$env:ALLOWED_ORIGINS = $origins
$env:AUTO_REBUILD_INDEX = "false"   # skip the slow index check at start-up (rebuild by hand after new circulars)

Set-Location $backend
# One process only: conversation memory, the rate limiter and the embedding model live in-process.
# "python -m uvicorn" (not uvicorn.exe): the .exe launchers keep the path of the folder the venv was created in.
& (Join-Path $backend ".venv\Scripts\python.exe") -m uvicorn app.main:app --host 127.0.0.1 --port 8000 `
    --proxy-headers --forwarded-allow-ips 127.0.0.1
