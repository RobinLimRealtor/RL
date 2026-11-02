@echo off
title Auto Video Editor
cd /d "%~dp0"

rem ---- Find Python ----
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo  Python 3.10 or newer is needed and was not found.
  echo  Opening the download page now. Install it, tick "Add python.exe to PATH",
  echo  then double-click this file again.
  echo.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

rem ---- First-time setup (only slow the first time) ----
if not exist ".venv\Scripts\python.exe" (
  echo  Setting up for the first time. This takes a few minutes...
  %PY% -m venv .venv
  if errorlevel 1 goto failed
)
echo  Checking everything is installed...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto failed

rem ---- Run ----
".venv\Scripts\python.exe" -m autoedit
pause
exit /b 0

:failed
echo.
echo  Setup failed. Check your internet connection and try again.
echo  If it keeps failing, delete the ".venv" folder next to this file and retry.
pause
exit /b 1
