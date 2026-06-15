"""Парсер описи к реализации (XLSX от фулфилмент-компании).

Опись имеет один лист. Структура:
  - строка-заголовок:  "Опись к реализации №<номер> от <дата>"
  - блок «ключ: значение» (ключ в колонке A, значение в колонке C)
  - таблица (с шапкой "Контейнер/штрихкод | ... | Номенклатура | ... |
    Характеристика | ... | Количество | Масса, кг | Объем, м*3"):
        PAC<цифры> — короб; OZN<цифры> — товар в последнем коробе; "Итого" — игнор.

Парсер устойчив к небольшим вариациям: строки ищутся по содержимому
(регулярки/ключи), а индексы колонок таблицы определяются по её шапке.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import IO, Union

import openpyxl

from core.models import Box, Opis, Product

# --- паттерны и ключи -------------------------------------------------------

TITLE_RE = re.compile(r"Опись к реализации\s*№\s*(\d+)\s+от\s+(\d{2}\.\d{2}\.\d{4})")
PAC_RE = re.compile(r"^PAC\d+$", re.IGNORECASE)
OZN_RE = re.compile(r"^OZN\d+$", re.IGNORECASE)

# Ключи блока «ключ: значение» -> поле модели. Сравнение по нормализованному
# ключу (нижний регистр, без хвостового двоеточия и пробелов).
HEADER_KEYS = {
    "контрагент": "counterparty",
    "доставить до склада": "warehouse_ozon",
    "адрес склада мп": "warehouse_mp",
    "номер поставки вл/к клиента": "supply_order_number",
    "номер входящего документа": "upd_number",
}

# Индексы колонок таблицы по умолчанию (0-based), если шапку распознать не удалось.
DEFAULT_COLS = {"code": 0, "name": 3, "characteristic": 6, "quantity": 8, "weight": 9, "volume": 10}


class OpisParseError(ValueError):
    """Опись не удалось распарсить (нарушена структура / пустой файл)."""


def _norm(value) -> str:
    """Строковое значение ячейки -> нормализованный ключ."""
    if value is None:
        return ""
    return str(value).strip().rstrip(":").strip().lower()


def _to_float(value) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    txt = str(value).strip().replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(round(f)) if f is not None else None


def _detect_columns(header_row: list) -> dict[str, int]:
    """По строке-шапке таблицы определить индексы нужных колонок."""
    cols = dict(DEFAULT_COLS)
    for idx, cell in enumerate(header_row):
        h = _norm(cell)
        if not h:
            continue
        if "контейнер" in h or "штрихкод" in h:
            cols["code"] = idx
        elif "номенклатура" in h:
            cols["name"] = idx
        elif "характеристика" in h:
            cols["characteristic"] = idx
        elif "количество" in h:
            cols["quantity"] = idx
        elif "масса" in h:
            cols["weight"] = idx
        elif "объ" in h:  # объем / объём
            cols["volume"] = idx
    return cols


def parse_opis(source: Union[str, Path, IO[bytes]]) -> Opis:
    """Распарсить XLSX-опись в модель Opis.

    source — путь к файлу или открытый бинарный файловый объект
    (например, загруженный через Streamlit file_uploader).
    """
    wb = openpyxl.load_workbook(source, data_only=True, read_only=True)
    try:
        ws = wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()

    if not rows:
        raise OpisParseError("Файл описи пуст")

    # 1) Заголовок: номер и дата описи.
    opis_number = opis_date = None
    title_row_idx = 0
    for i, row in enumerate(rows):
        joined = " ".join(str(c) for c in row if c is not None)
        m = TITLE_RE.search(joined)
        if m:
            opis_number = m.group(1)
            d, mth, y = m.group(2).split(".")
            opis_date = date(int(y), int(mth), int(d))
            title_row_idx = i
            break
    if opis_number is None:
        raise OpisParseError(
            'Не найдена строка вида "Опись к реализации №… от …" — проверьте формат файла'
        )

    # 2) Найти строку-шапку таблицы (содержит "Контейнер/штрихкод").
    table_header_idx = None
    for i in range(title_row_idx + 1, len(rows)):
        if any("контейнер" in _norm(c) or "штрихкод" in _norm(c) for c in rows[i]):
            table_header_idx = i
            break
    if table_header_idx is None:
        raise OpisParseError('Не найдена шапка таблицы ("Контейнер/штрихкод")')

    cols = _detect_columns(rows[table_header_idx])

    # 3) Блок «ключ: значение» между заголовком и шапкой таблицы.
    fields: dict[str, str] = {}
    for row in rows[title_row_idx + 1 : table_header_idx]:
        if not row:
            continue
        key = _norm(row[0])
        field = HEADER_KEYS.get(key)
        if not field:
            continue
        # Значение — первая непустая ячейка после ключа (обычно колонка C).
        value = next(
            (str(c).strip() for c in row[1:] if c is not None and str(c).strip()), None
        )
        if value:
            fields[field] = value

    if not fields.get("supply_order_number"):
        raise OpisParseError(
            'В описи не найден "Номер поставки вл/к клиента" — без него нельзя '
            "сопоставить с заявкой Ozon"
        )

    # 4) Тело таблицы: PAC -> короб, OZN -> товар, "Итого" -> стоп.
    boxes: list[Box] = []
    current: Box | None = None

    def cell(row, key):
        idx = cols[key]
        return row[idx] if idx < len(row) else None

    for row in rows[table_header_idx + 1 :]:
        if not row:
            continue
        code = cell(row, "code")
        code = str(code).strip() if code is not None else ""
        if not code:
            continue
        low = code.lower()
        if low.startswith("итог"):
            break
        if PAC_RE.match(code):
            current = Box(
                pac_code=code.upper(),
                products=[],
                weight_kg=_to_float(cell(row, "weight")),
                volume_m3=_to_float(cell(row, "volume")),
            )
            boxes.append(current)
        elif OZN_RE.match(code):
            if current is None:
                raise OpisParseError(
                    f"Строка товара {code} встретилась раньше первого короба PAC"
                )
            qty = _to_int(cell(row, "quantity"))
            if qty is None or qty < 1:
                raise OpisParseError(f"У товара {code} некорректное количество: {qty!r}")
            name = cell(row, "name")
            char = cell(row, "characteristic")
            current.products.append(
                Product(
                    ozn_sku=code.upper(),
                    name=str(name).strip() if name else None,
                    characteristic=str(char).strip() if char else None,
                    quantity=qty,
                )
            )
        # прочие строки (пустые служебные) — пропускаем

    if not boxes:
        raise OpisParseError("В таблице описи не найдено ни одного короба PAC")

    return Opis(
        opis_number=opis_number,
        opis_date=opis_date,
        counterparty=fields.get("counterparty"),
        warehouse_ozon=fields.get("warehouse_ozon"),
        warehouse_mp=fields.get("warehouse_mp"),
        supply_order_number=fields["supply_order_number"],
        upd_number=fields.get("upd_number"),
        boxes=boxes,
    )
