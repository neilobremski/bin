@echo off
setlocal
set "BIN_DIR=%~dp0"
set "RUNNER=%BIN_DIR%lib\venv_exec.py"
set "B3T=%BIN_DIR%apps\b3t\__main__.py"

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python "%RUNNER%" b3t "%B3T%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 "%RUNNER%" b3t "%B3T%" %*
    exit /b %ERRORLEVEL%
)

echo b3t: python not found on PATH >&2
exit /b 127
