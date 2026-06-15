@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv (
    echo Первый запуск: создаю окружение и устанавливаю библиотеки. Это займёт ~3 минуты...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo Проверяю обновления...
python updater.py

streamlit run app.py
pause
