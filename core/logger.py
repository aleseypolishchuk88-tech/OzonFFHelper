"""Настройка логирования: app.log (общий) и api.log (request/response Ozon).

Api-Key в логи не пишется целиком — маскируется.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from core.config import LOGS_DIR, ensure_dirs

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    ensure_dirs()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    app_handler = RotatingFileHandler(
        LOGS_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    app_handler.setFormatter(fmt)

    root = logging.getLogger("ozonff")
    root.setLevel(level)
    root.addHandler(app_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # Отдельный лог для сырых request/response Ozon API.
    api_handler = RotatingFileHandler(
        LOGS_DIR / "api.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    api_handler.setFormatter(fmt)
    api_logger = logging.getLogger("ozonff.api")
    api_logger.addHandler(api_handler)
    api_logger.propagate = True  # дублируется и в app.log

    _configured = True


def get_logger(name: str = "ozonff") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
