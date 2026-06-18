@echo off
setlocal
set "APP_DIR=%~dp0apps\b3t"
set "VENV=%APP_DIR%\.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo b3t: creating venv... >&2
    python -m venv "%VENV%"
    "%VENV%\Scripts\pip" install -q -r "%APP_DIR%\requirements.txt"
)
"%VENV%\Scripts\python.exe" "%APP_DIR%\__main__.py" %*
