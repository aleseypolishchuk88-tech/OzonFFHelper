"""Сохранение файлов прямо на рабочий стол — минуя загрузки браузера.

Зачем: браузер кладёт скачанный файл на рабочий стол мимо Проводника,
и тот не перерисовывает значки — файл на диске есть, а глазами его не видно.
Здесь мы пишем файл сами и сообщаем оболочке Windows, что папка изменилась.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

# Константы shell32
_SHCNE_UPDATEDIR = 0x00001000
_SHCNE_CREATE = 0x00000002
_SHCNF_PATHW = 0x0005


def desktop_dir() -> Path:
    """Реальная папка рабочего стола (учитывает перенос в OneDrive и т.п.)."""
    # Основной путь — через реестр, он знает о переносе папки
    try:
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            path = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
            if path.is_dir():
                return path
    except Exception:
        pass

    for candidate in (
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "OneDrive" / "Рабочий стол",
    ):
        if candidate.is_dir():
            return candidate
    return Path.home()


def _notify_shell(path: Path) -> None:
    """Сказать Проводнику, что в папке появился файл — иначе значок не появится."""
    try:
        shell = ctypes.windll.shell32
        shell.SHChangeNotify(_SHCNE_CREATE, _SHCNF_PATHW, ctypes.c_wchar_p(str(path)), None)
        shell.SHChangeNotify(
            _SHCNE_UPDATEDIR, _SHCNF_PATHW, ctypes.c_wchar_p(str(path.parent)), None
        )
    except Exception:
        pass  # не критично: файл всё равно сохранён


def _unique_path(path: Path) -> Path:
    """Не затирать уже существующий файл: добавить (2), (3)…"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(2, 100):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return path


def save_to_desktop(filename: str, data: bytes) -> Path:
    """Записать файл на рабочий стол и показать его Проводнику. Вернуть путь."""
    target = _unique_path(desktop_dir() / filename)
    target.write_bytes(data)
    _notify_shell(target)
    return target


def reveal_in_explorer(path: Path) -> None:
    """Открыть Проводник с выделенным файлом."""
    try:
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    except Exception:
        pass
