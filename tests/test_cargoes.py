"""Тесты сборки грузомест и сверки описи с составом поставки."""
from core.cargoes import build_cargoes, compare_opis_to_content, content_increase_issues
from core.models import Box, Opis, Product, SupplyContentItem


def _opis(products_by_box):
    boxes = [
        Box(pac_code=pac, products=[Product(ozn_sku=s, quantity=q) for s, q in items])
        for pac, items in products_by_box
    ]
    return Opis(
        opis_number="1", opis_date="2026-06-13", supply_order_number="2000000000001",
        boxes=boxes,
    )


def _content(items):
    return [SupplyContentItem(sku=s, offer_id=f"OF{s}", name="x", quantity=q, barcode=f"OZN{s}")
            for s, q in items]


def test_match_no_issues():
    opis = _opis([("PAC1", [("OZN101", 10)]), ("PAC2", [("OZN202", 5)])])
    content = _content([(101, 10), (202, 5)])
    assert compare_opis_to_content(opis, content) == []


def test_quantity_mismatch():
    opis = _opis([("PAC1", [("OZN101", 8)])])
    content = _content([(101, 10)])
    issues = compare_opis_to_content(opis, content)
    assert len(issues) == 1 and "OZN101" in issues[0] and "8" in issues[0] and "10" in issues[0]


def test_missing_and_extra():
    opis = _opis([("PAC1", [("OZN101", 10)]), ("PAC2", [("OZN303", 3)])])
    content = _content([(101, 10), (202, 5)])
    issues = compare_opis_to_content(opis, content)
    assert any("OZN303" in i for i in issues)  # есть в описи, нет в составе
    assert any("OZN202" in i for i in issues)  # есть в составе, нет в описи


def test_increase_detected():
    # опись 5, заявка 4 -> увеличение, выравнивать нельзя
    opis = _opis([("PAC1", [("OZN101", 5)])])
    content = _content([(101, 4)])
    inc = content_increase_issues(opis, content)
    assert len(inc) == 1 and "OZN101" in inc[0]


def test_decrease_allowed():
    # опись 4, заявка 5 -> уменьшение, увеличений нет (выравнивание возможно)
    opis = _opis([("PAC1", [("OZN101", 4)])])
    content = _content([(101, 5)])
    assert content_increase_issues(opis, content) == []


def test_sku_missing_in_supply_is_increase():
    opis = _opis([("PAC1", [("OZN999", 3)])])
    content = _content([(101, 5)])
    inc = content_increase_issues(opis, content)
    assert any("OZN999" in i for i in inc)


def test_build_cargoes_uses_barcode_offer():
    opis = _opis([("PAC1", [("OZN101", 10)])])
    content = _content([(101, 10)])
    cargoes, warns = build_cargoes(opis, content)
    assert warns == []
    assert cargoes[0]["key"] == "PAC1"
    assert cargoes[0]["value"]["type"] == "BOX"
    item = cargoes[0]["value"]["items"][0]
    assert item["barcode"] == "OZN101" and item["offer_id"] == "OF101" and item["quantity"] == 10
