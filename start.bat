@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Installing OzonFFHelper... first run takes about 3 minutes, please wait.
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

echo Checking for updates...
python updater.py

echo Starting OzonFFHelper. Browser: http://localhost:8501
echo Do not close this window while you work.
streamlit run app.py
pause
