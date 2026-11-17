@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import fastapi,uvicorn,httpx,pypdf,multipart" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" start.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Startup failed. Check Python installation and the error above.
pause
exit /b 1
