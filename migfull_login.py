"""Разовый вход в migfull для робота: открывает окно, ждёт, пока вы войдёте и закроете его."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.migfull_rpa import open_for_login

if __name__ == "__main__":
    open_for_login()
    print("Готово. Сессия сохранена.")
