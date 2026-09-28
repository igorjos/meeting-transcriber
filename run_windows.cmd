@echo off
rem Double-click this file to run the transcriber - no PowerShell knowledge
rem needed. It runs run_windows.ps1 for you, working around the two things
rem that otherwise make that file's window flash open and closed instantly:
rem   - PowerShell blocking a script that was downloaded from the internet
rem     ("Mark of the Web") - Unblock-File clears that below.
rem   - PowerShell's default script-execution policy - "-ExecutionPolicy
rem     Bypass" below applies only to this one run, nothing is changed
rem     system-wide.
rem This window is also kept open (see "pause" below) even if run_windows.ps1
rem fails before it gets a chance to print its own reason and wait for Enter.
setlocal
title Transcriber
cd /d "%~dp0"

powershell -NoProfile -Command "Unblock-File -LiteralPath '%~dp0run_windows.ps1'" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_windows.ps1"

echo.
echo (This window stays open so you can read any messages above.)
pause
