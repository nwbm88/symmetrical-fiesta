@echo off
REM ---------------------------------------------------------------------
REM  Updates yt-dlp.  YouTube changes often and downloads start failing
REM  when yt-dlp gets stale - run this when that happens.
REM ---------------------------------------------------------------------
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run run.bat first to set things up.
    pause
    exit /b 1
)

echo Updating yt-dlp...
".venv\Scripts\python.exe" -m pip install --upgrade yt-dlp
echo.
echo Done.
pause
endlocal
