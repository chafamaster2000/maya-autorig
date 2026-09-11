@echo off
rem maya-autorig - Windows installer.
rem Double-click this file, or run it from a shell:
rem     install.bat              install everything installable
rem     install.bat -DryRun      show what it would do, change nothing
rem     install.bat -SkipPrereqs do not install Python / Node / Claude Code / Codex
rem     install.bat -SkipCodex   configure Claude Code only (-SkipClaude: Codex only)
rem
rem It only wraps tools\install_windows.ps1 with an execution policy that lets
rem it run: a freshly downloaded .ps1 is blocked by default on Windows, which
rem is the usual reason "nothing happens" when you double-click one.

setlocal
set "PS1=%~dp0tools\install_windows.ps1"

if not exist "%PS1%" (
    echo ERROR: cannot find "%PS1%"
    echo Run this from the folder you cloned, not from a copy of the .bat alone.
    goto :end
)

rem PowerShell 7 when it is there, the built-in 5.1 otherwise. Both work.
where pwsh.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
)
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (echo Done.) else (echo Finished with errors; see the summary above.)

:end
rem Keep the window open when this was double-clicked from Explorer.
echo %CMDCMDLINE% | find /i "/c" >nul
if %ERRORLEVEL% EQU 0 pause
exit /b %RC%
