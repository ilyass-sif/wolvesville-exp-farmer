# start_exp_farmer.ps1 — Windows PowerShell script for Wolvesville EXP Farmer stack

# Activate virtualenv if present
if (Test-Path ".venv\Scripts\Activate.ps1") {
    . .venv\Scripts\Activate.ps1
}

Write-Host "[*] Starting Wolvesville headless browser token grabber..." -ForegroundColor Cyan
# Start headless.py in the background, redirecting stdout/stderr to headless.log
$headlessProcess = Start-Process python -ArgumentList "headless.py" -NoNewWindow -PassThru -RedirectStandardOutput "headless.log" -RedirectStandardError "headless.log"

Write-Host "[*] Starting Wolvesville EXP Farmer..." -ForegroundColor Green
try {
    python exp_farmer.py
} finally {
    Write-Host "[*] Shutting down headless browser..." -ForegroundColor Yellow
    if ($headlessProcess -and -not $headlessProcess.HasExited) {
        Stop-Process -Id $headlessProcess.Id -Force
    }
}
