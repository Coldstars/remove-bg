@echo off
setlocal
cd /d "%~dp0"

if exist "%LOCALAPPDATA%\Programs\Python\Python312-paddle\python.exe" (
  set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312-paddle\python.exe"
) else (
  set "PYTHON=python"
)

echo Installing PyTorch CUDA 12.8 runtime...
"%PYTHON%" -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto :failed

echo Installing BiRefNet GPU dependencies...
"%PYTHON%" -m pip install -r requirements.txt -r requirements-gpu.txt
if errorlevel 1 goto :failed

echo Checking CUDA device...
"%PYTHON%" -c "import torch; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('CUDA ready:', torch.cuda.get_device_name(0))"
if errorlevel 1 goto :failed

echo.
echo GPU setup completed. Start the GUI with start-gui.bat.
pause
exit /b 0

:failed
echo.
echo GPU setup failed. Check Python, network access, and the NVIDIA driver.
pause
exit /b 1
