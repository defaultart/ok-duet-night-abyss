@echo off
setlocal EnableExtensions

set "PROJECT_DIR=E:\Project\ok-duet-night-abyss"
set "PYTHON_EXE=F:\ok-dna\data\apps\ok-dna\python\python.exe"
set "OK_RUNTIME=F:\ok-dna\data\apps\ok-dna\working"

rem PostMessage input needs the same administrator level as the game.
fltmc >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found: %PYTHON_EXE%
    pause
    exit /b 1
)

if not exist "%OK_RUNTIME%" (
    echo [ERROR] OK-DNA runtime not found: %OK_RUNTIME%
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\main.py" (
    echo [ERROR] Project entry not found: %PROJECT_DIR%\main.py
    pause
    exit /b 1
)

set "PYTHONPATH=%OK_RUNTIME%"
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ERROR] Cannot enter project directory: %PROJECT_DIR%
    pause
    exit /b 1
)

echo Starting OK-DNA from source...
echo Project: %PROJECT_DIR%
echo Close this window or press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" "%PROJECT_DIR%\main.py"
set "APP_EXIT_CODE=%ERRORLEVEL%"

echo.
echo OK-DNA exited with code: %APP_EXIT_CODE%
pause
exit /b %APP_EXIT_CODE%
