"""Загрузка и валидация настроек из .env."""
from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values, set_key
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_API_BASE = "https://api-seller.ozon.ru"


class Settings(BaseModel):
    """Настройки приложения, прочитанные из .env."""

    client_id: str = Field(default="")
    api_key: str = Field(default="")
    api_base: str = Field(default=DEFAULT_API_BASE)

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.api_key)


def load_settings() -> Settings:
    """Прочитать .env (без загрязнения os.environ)."""
    values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return Settings(
        client_id=(values.get("OZON_CLIENT_ID") or "").strip(),
        api_key=(values.get("OZON_API_KEY") or "").strip(),
        api_base=(values.get("OZON_API_BASE") or DEFAULT_API_BASE).strip(),
    )


def save_credentials(client_id: str, api_key: str) -> None:
    """Сохранить ключи в .env (создаёт файл при отсутствии)."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text(
            "# Ключи Ozon Seller API (локальный файл, в git не попадает)\n",
            encoding="utf-8",
        )
    set_key(str(ENV_PATH), "OZON_CLIENT_ID", client_id.strip(), quote_mode="never")
    set_key(str(ENV_PATH), "OZON_API_KEY", api_key.strip(), quote_mode="never")


def mask_key(key: str) -> str:
    """Замаскировать ключ для логов: первые 4 + *** + последние 4."""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}***{key[-4:]}"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
