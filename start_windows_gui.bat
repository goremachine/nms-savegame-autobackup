@echo off
setlocal

REM This script activates the Python virtual environment and then starts the
REM Atlas Archive tool with the graphical user interface (GUI).

REM --- Configuration ---
REM Set paths relative to this script's location.
set "VENV_ACTIVATE=%~dp0.venv\Scripts\activate.bat"
set "PYTHON_SCRIPT=%~dp0autobackup.py"
set "CONFIG_FILE=%~dp0config.json"
set "REQUIREMENTS_FILE=%~dp0requirements.txt"

REM --- Check for Virtual Environment and create if missing ---
if not exist "%VENV_ACTIVATE%" (
    echo Virtual environment not found. Creating it now...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create the virtual environment.
        echo Please ensure Python is installed and in your system's PATH.
        pause
        exit /b
    )
)

REM --- Activate and Run ---
echo Activating virtual environment...
call "%VENV_ACTIVATE%"

REM --- Install/Update Dependencies ---
echo Installing dependencies from requirements.txt...
pip install -r "%REQUIREMENTS_FILE%"

echo Starting Atlas Archive...
start "Atlas_Archive" /B pythonw.exe "%PYTHON_SCRIPT%" "%CONFIG_FILE%"