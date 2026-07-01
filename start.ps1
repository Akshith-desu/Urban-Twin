# Run from anywhere — paths are resolved relative to this script's location
$root = $PSScriptRoot

$venvPython = Join-Path $root "venv\Scripts\python.exe"
$srcDir     = Join-Path $root "src"
$frontendDir = Join-Path $root "frontend"

Write-Host "Starting FastAPI server..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$srcDir'; & '$venvPython' -m uvicorn api_server:app --reload --port 8000"

Write-Host "Starting Next.js frontend..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontendDir'; npm run dev"

Write-Host "Both servers have been launched in separate windows."