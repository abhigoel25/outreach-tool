@echo off
REM ============================================================
REM  run_daily.bat - Daily outreach automation
REM  Scheduled via Windows Task Scheduler to run at 10:00 AM.
REM  Logs are written to: logs\outreach_YYYY-MM-DD.log
REM ============================================================

set PROJECT_DIR=C:\Users\abhin\OneDrive\Desktop\Connections\outreach-tool
set PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe

cd /d "%PROJECT_DIR%"

echo [%DATE% %TIME%] Starting daily outreach...
"%PYTHON%" run_daily.py --headless
echo [%DATE% %TIME%] Done.