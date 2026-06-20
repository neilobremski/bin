@echo off
setlocal
set "BIN_DIR=%~dp0"
set "N0B=%BIN_DIR%apps\n0b\n0b.py"

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python "%N0B%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 "%N0B%" %*
    exit /b %ERRORLEVEL%
)

echo n0b: python not found on PATH >&2
exit /b 127
