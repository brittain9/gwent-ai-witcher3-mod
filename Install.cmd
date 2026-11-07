@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 scripts\setup_player.py %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Install Python 3.12 or newer from python.org, then run Install.cmd again.
        pause
        exit /b 1
    )
    python scripts\setup_player.py %*
)
set "setup_result=%errorlevel%"
if not "%setup_result%"=="0" echo Setup stopped. Read the message above before retrying.
pause
exit /b %setup_result%
