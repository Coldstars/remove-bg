@echo off
setlocal
cd /d "%~dp0"

if exist "%LOCALAPPDATA%\Programs\Python\Python312-paddle\python.exe" (
  set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312-paddle\python.exe"
) else (
  set "PYTHON=python"
)

"%PYTHON%" src\gui_server.py
if errorlevel 1 (
  echo.
  echo Failed to start GUI. Press any key to close.
  pause >nul
  exit /b %errorlevel%
)
