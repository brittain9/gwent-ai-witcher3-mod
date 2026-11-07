@echo off
call "%~dp0Install.cmd" --uninstall %*
exit /b %errorlevel%
