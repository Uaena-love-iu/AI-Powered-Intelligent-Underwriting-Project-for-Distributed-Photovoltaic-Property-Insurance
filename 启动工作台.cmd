@echo off
chcp 65001 >nul
set PYTHONUTF8=1
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0start_workbench.py" %*
if errorlevel 1 pause
popd
