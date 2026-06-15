"""Автообновление OzonFFHelper из GitHub-репозитория.

Запускается из start.bat перед стартом приложения. Логика:
  1. Читает локальную версию (файл VERSION) и удалённую (raw VERSION из репозитория).
  2. Если версии совпадают или интернета нет — ничего не делает, запускает текущую.
  3. Если удалённая новее — скачивает zip репозитория, обновляет файлы кода
     (app.py, core/, requirements.txt, README.md, VERSION, updater.py),
     НЕ трогая .env, .venv, data/ и logs/. Затем доустанавливает зависимости.

Любая ошибка обновления НЕ мешает запуску — программа просто стартует на текущей версии.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- НАСТРОЙКА РЕПОЗИТОРИЯ (заполняется после создания репозитория на GitHub) ---
REPO_OWNER = "aleseypolishchuk88-tech"
REPO_NAME = "OzonFFHelper"
BRANCH = "main"

# Файлы/папки, которые обновляются (код). Остальное (.env, .venv, data, logs) не трогаем.
UPDATE_PATHS = ["app.py", "updater.py", "requirements.txt", "README.md", "VERSION", "core"]


def _raw_version_url() -> str:
    return f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/VERSION"


def _zip_url() -> str:
    return f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{BRANCH}.zip"


def _fetch(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "OzonFFHelper-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _local_version() -> str:
    f = ROOT / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "0"


def main() -> None:
    if not REPO_OWNER:
        print("[обновление] репозиторий не настроен — пропускаю проверку.")
        return
    try:
        remote_v = _fetch(_raw_version_url(), timeout=10).decode("utf-8").strip()
    except Exception as e:  # noqa
        print(f"[обновление] не удалось проверить ({e}) — запускаю текущую версию.")
        return

    if remote_v == _local_version():
        print(f"[обновление] установлена актуальная версия {remote_v}.")
        return

    print(f"[обновление] доступна версия {remote_v} (текущая {_local_version()}) — обновляю…")
    tmp = ROOT / "_update_tmp"
    try:
        data = _fetch(_zip_url(), timeout=60)
        if tmp.exists():
            shutil.rmtree(tmp)
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        inner = next(p for p in tmp.iterdir() if p.is_dir())  # папка <repo>-<branch>
        for rel in UPDATE_PATHS:
            src, dst = inner / rel, ROOT / rel
            if not src.exists():
                continue
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        shutil.rmtree(tmp)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
            check=False,
        )
        print(f"[обновление] готово — версия {remote_v}.")
    except Exception as e:  # noqa
        print(f"[обновление] ошибка ({e}) — запускаю текущую версию.")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
