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
if errorlevel 1 goto :failed
echo.
echo Done - yt-dlp is up to date.
pause
exit /b 0

:failed
echo.
echo   The update failed - see the messages above.
echo   Usually that means no internet connection, or a proxy/firewall
echo   blocking it. If it persists, delete the .venv folder and run
echo   run.bat again to rebuild it from scratch.
echo.
pause
exit /b 1
