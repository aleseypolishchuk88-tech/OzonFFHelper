@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

REM Create a virtual environment if missing
if not exist ".venv\Scripts\python.exe" (
    echo Creating environment...
    python -m venv .venv
)

REM Use venv python if available, otherwise fall back to system python
set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

REM Install libraries only if streamlit is not available yet
"%PYEXE%" -c "import streamlit" 1>nul 2>nul
if errorlevel 1 (
    echo Installing libraries... first run takes about 3 minutes, please wait.
    "%PYEXE%" -m pip install --upgrade pip
    "%PYEXE%" -m pip install -r requirements.txt
)

REM Skip Streamlit first-run email prompt
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
    (
        echo [general]
        echo email = ""
    ) > "%USERPROFILE%\.streamlit\credentials.toml"
)

echo Checking for updates...
"%PYEXE%" updater.py

echo Starting OzonFFHelper. Browser will open at http://localhost:8501
echo Do not close this window while you work.
"%PYEXE%" -m streamlit run app.py
pause
