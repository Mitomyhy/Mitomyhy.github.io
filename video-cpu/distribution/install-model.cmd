@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONNOUSERSITE=1"
if not exist "%~dp0python\python.exe" (
  echo Extract the complete MITOM app ZIP before running this file.
  pause
  exit /b 1
)
"%~dp0python\python.exe" -I "%~dp0setup_model.py"
set "MITOM_SETUP_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %MITOM_SETUP_EXIT%
