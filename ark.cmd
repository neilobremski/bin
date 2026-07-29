@echo off
setlocal
set "BIN_DIR=%~dp0"
set "ARK=%BIN_DIR%apps\ark\ark.py"

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python "%ARK%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 "%ARK%" %*
    exit /b %ERRORLEVEL%
)

echo ark: python not found on PATH >&2
exit /b 127
