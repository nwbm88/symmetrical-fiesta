@echo off
REM ---------------------------------------------------------------------
REM  Live Show Archiver - Windows launcher
REM  Double-click this file to start the app.  The first run sets up a
REM  private Python environment in .venv; later runs just start the app.
REM ---------------------------------------------------------------------
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "VENV=.venv"
set "PYEXE=%VENV%\Scripts\python.exe"
set "PYWEXE=%VENV%\Scripts\pythonw.exe"

if exist "%PYEXE%" goto :check_ffmpeg

REM ---------------------------------------------------- first-run setup
echo First run: setting up the Python environment. This takes a minute...
echo.

set "BOOTSTRAP="
py -3 --version >nul 2>&1
if not errorlevel 1 set "BOOTSTRAP=py -3"
if defined BOOTSTRAP goto :have_python

python --version >nul 2>&1
if not errorlevel 1 set "BOOTSTRAP=python"
if defined BOOTSTRAP goto :have_python

echo   Python was not found on this computer.
echo.
echo   Install Python 3.9 or newer from https://www.python.org/downloads/
echo   IMPORTANT: tick "Add python.exe to PATH" in the installer, then
echo   run this file again.
echo.
pause
exit /b 1

:have_python
%BOOTSTRAP% -m venv "%VENV%"
if errorlevel 1 goto :venv_failed

"%PYEXE%" -m pip install --upgrade pip >nul 2>&1
echo Installing required packages (yt-dlp, PySide6, requests)...
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :deps_failed
echo.
echo Setup complete.
echo.
goto :check_ffmpeg

:venv_failed
echo.
echo   Could not create the Python environment in "%VENV%".
echo   Check that you can write to this folder and try again.
echo.
pause
exit /b 1

:deps_failed
echo.
echo   Installing the required packages failed - see the messages above.
echo   A common cause is no internet connection or a proxy/firewall.
echo.
pause
exit /b 1

REM ------------------------------------------------------- ffmpeg notice
:check_ffmpeg
where ffmpeg >nul 2>&1
if not errorlevel 1 goto :launch
if exist "ffmpeg\bin\ffmpeg.exe" goto :launch
echo NOTE: ffmpeg was not found. Downloading works without it, but
echo       extracting and splitting audio does not.
echo       Get the "release full" build from https://www.gyan.dev/ffmpeg/builds/
echo       then either add its bin folder to PATH, or unzip it here so that
echo       "%~dp0ffmpeg\bin\ffmpeg.exe" exists.
echo.

REM -------------------------------------------------------------- launch
:launch
REM Fail early with a readable message if the app can't even be imported.
"%PYEXE%" -c "import livearchiver" 2>nul
if errorlevel 1 goto :import_failed

if exist "%PYWEXE%" goto :launch_windowed
"%PYEXE%" -m livearchiver.cli gui
goto :eof

:launch_windowed
REM pythonw runs without a console window; the console we were started
REM from closes immediately.
start "" "%PYWEXE%" -m livearchiver.cli gui
goto :eof

:import_failed
echo.
echo   The app could not be loaded. Full error follows:
echo.
"%PYEXE%" -c "import livearchiver"
echo.
pause
exit /b 1
