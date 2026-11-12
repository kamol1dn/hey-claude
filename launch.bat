@echo off
rem Double-click to start hey-claude in the background (no console window).
rem   launch.bat           start it (if it's already running, it just shows itself)
rem   launch.bat restart   quit the running one first, then start it again
rem Logs: state\logs\app.log. To watch them live, use run.cmd from a terminal instead.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo hey-claude isn't set up here yet: .venv\Scripts\pythonw.exe is missing.
    echo See README, "Install", to create the virtual environment.
    pause
    exit /b 1
)

if /i "%~1"=="restart" (
    echo Stopping hey-claude...
    rem the venv's pythonw (its /T takes the real interpreter it starts with it)
    powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match '\.venv\\Scripts\\pythonw\.exe.*-m heyclaude\s*$' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }"
    ping -n 3 127.0.0.1 >nul
)

start "" ".venv\Scripts\pythonw.exe" -m heyclaude
endlocal
