"""Тесты парсера описи на реальном файле tests/fixtures/Опись_sample.xlsx."""
from datetime import date
from pathlib import Path

import pytest

from core.opis_parser import OpisParseError, parse_opis

FIXTURE = Path(__file__).parent / "fixtures" / "Опись_sample.xlsx"


@pytest.fixture(scope="module")
def opis():
    return parse_opis(FIXTURE)


def test_header_fields(opis):
    assert opis.opis_number == "62089"
    assert opis.opis_date == date(2026, 5, 15)
    assert opis.supply_order_number == "2000052024672"
    assert "ИВАНОВО" in opis.warehouse_ozon.upper()
    assert opis.counterparty  # ФИО заполнено


def test_three_boxes(opis):
    """Ключевое требование брифа: ровно 3 короба."""
    assert opis.total_boxes == 3
    assert [b.pac_code for b in opis.boxes] == [
        "PAC200551831",
        "PAC200551833",
        "PAC200551835",
    ]


def test_products_and_quantities(opis):
    skus = [p.ozn_sku for b in opis.boxes for p in b.products]
    assert skus == ["OZN891162680", "OZN1018198144", "OZN1192644615"]
    # по 10 шт в каждом коробе, всего 30
    assert all(b.total_quantity == 10 for b in opis.boxes)
    assert opis.total_quantity == 30


def test_box_metrics(opis):
    first = opis.boxes[0]
    assert first.weight_kg == pytest.approx(2.9)
    assert first.volume_m3 == pytest.approx(0.038)


def test_ignores_itogo_row(opis):
    # "Итого" не должно стать коробом/товаром
    assert all("ИТОГ" not in b.pac_code.upper() for b in opis.boxes)


def test_bad_file_raises(tmp_path):
    empty = tmp_path / "empty.xlsx"
    import openpyxl

    openpyxl.Workbook().save(empty)
    with pytest.raises(OpisParseError):
        parse_opis(empty)
