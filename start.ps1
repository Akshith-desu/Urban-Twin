# Run from the root directory (c:\Users\skrpf\EL)
# Start the FastAPI server
Write-Host "Starting FastAPI server..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd src; python3 -m uvicorn api_server:app --reload --port 8000"

# Start the Next.js development server
Write-Host "Starting Next.js frontend..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd frontend; npm run dev"

Write-Host "Both servers have been launched in separate windows."
