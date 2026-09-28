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

if not exist "%~dp0run_windows.ps1" (
    echo.
    echo ERROR: run_windows.ps1 is missing from this folder.
    echo.
    echo This usually means this file was opened straight from inside the
    echo .zip archive, or from a SharePoint/OneDrive location that has not
    echo finished syncing - only this one file gets copied out, not the
    echo rest of the project.
    echo.
    echo Fix: close this window, right-click the meeting-transcriber .zip
    echo file and choose Extract All, then run run_windows.cmd from the
    echo extracted folder ^(not from inside the zip preview^).
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -Command "Unblock-File -LiteralPath '%~dp0run_windows.ps1'" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_windows.ps1"

echo.
echo (This window stays open so you can read any messages above.)
pause
