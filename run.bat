@echo off
setlocal
cd /d "%~dp0"

if not exist "input" mkdir "input"
if not exist "output" mkdir "output"

python src\batch_remove_bg.py %*
if errorlevel 1 (
  echo.
  echo Failed. Press any key to close.
  pause >nul
  exit /b %errorlevel%
)

echo.
echo Done. Results are in the output folder.
pause
