@echo off
title Auto Video Editor
cd /d "%~dp0"
rem Nothing to install first: the first start downloads everything it needs
rem (including its own copy of Python) into this folder. No admin needed.

set "TOOLS=%~dp0.tools"
set "UV=%TOOLS%\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%TOOLS%\python"
set "UV_CACHE_DIR=%TOOLS%\cache"

if not exist "%UV%" (
  echo  First start: downloading the setup helper ^(one time only^)...
  if not exist "%TOOLS%" mkdir "%TOOLS%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_INSTALL_DIR=$env:TOOLS; $env:UV_NO_MODIFY_PATH='1'; $env:INSTALLER_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex" >nul
  if not exist "%UV%" goto failed
)

rem A working .venv on Python 3.10+? Otherwise (re)create it with uv's own Python.
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo  Setting up Python for the editor ^(one time only, a few minutes^)...
  if exist ".venv" rmdir /s /q ".venv"
  "%UV%" venv --quiet --python 3.12 --python-preference only-managed .venv
  if errorlevel 1 goto failed
)

echo  Checking everything is installed...
"%UV%" pip install --quiet --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto failed

".venv\Scripts\python.exe" -m autoedit
pause
exit /b 0

:failed
echo.
echo  Setup didn't finish. Check your internet connection and double-click this file again.
echo  If it keeps failing, delete the ".tools" and ".venv" folders next to this file and retry.
pause
exit /b 1
