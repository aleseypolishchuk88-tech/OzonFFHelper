"""Сборка тела запроса cargoes/create из описи ФФ и состава поставки Ozon.

Опись даёт структуру коробов (PAC) и какие OZN-коды с каким количеством лежат
в каждом. Состав поставки (bundle) даёт по каждому sku точные barcode/offer_id/quant,
которые ждёт Ozon. Сопоставляем по sku (OZN<sku>).
"""
from __future__ import annotations

from core.models import Opis, SupplyContentItem

CARGO_TYPE_BOX = "BOX"


def ozn_to_sku(ozn_code: str) -> int | None:
    """OZN1192644615 -> 1192644615."""
    s = ozn_code.strip().upper()
    if s.startswith("OZN") and s[3:].isdigit():
        return int(s[3:])
    return None


def build_cargoes(opis: Opis, content: list[SupplyContentItem]) -> tuple[list[dict], list[str]]:
    """Собрать массив cargoes для /v1/cargoes/create.

    Возвращает (cargoes, warnings). Каждый PAC-короб описи → одно грузоместо
    типа BOX; состав — товары короба с barcode/offer_id из состава поставки.
    """
    by_sku = {it.sku: it for it in content}
    cargoes: list[dict] = []
    warnings: list[str] = []

    for box in opis.boxes:
        items: list[dict] = []
        for p in box.products:
            sku = ozn_to_sku(p.ozn_sku)
            ref = by_sku.get(sku) if sku is not None else None
            if ref is None:
                warnings.append(
                    f"{box.pac_code}: товар {p.ozn_sku} не найден в составе поставки Ozon"
                )
                item = {"barcode": p.ozn_sku, "offer_id": "", "quant": 1, "quantity": p.quantity}
            else:
                item = {
                    "barcode": ref.barcode or p.ozn_sku,
                    "offer_id": ref.offer_id or "",
                    "quant": 1,
                    "quantity": p.quantity,
                }
            items.append(item)
        cargoes.append({"key": box.pac_code, "value": {"items": items, "type": CARGO_TYPE_BOX}})

    return cargoes, warnings


def build_content_items(opis: Opis) -> list[dict]:
    """Товарный состав для /v1/supply-order/content/update из описи.

    Суммирует количество по каждому sku (OZN) во всей описи.
    """
    agg: dict[int, int] = {}
    for box in opis.boxes:
        for p in box.products:
            sku = ozn_to_sku(p.ozn_sku)
            if sku is None:
                continue
            agg[sku] = agg.get(sku, 0) + p.quantity
    return [{"sku": sku, "quant": 1, "quantity": q} for sku, q in agg.items()]


def content_increase_issues(opis: Opis, content: list[SupplyContentItem]) -> list[str]:
    """Расхождения, требующие УВЕЛИЧЕНИЯ состава заявки (Ozon их не разрешает).

    Возвращает сообщения по тем sku, где в описи количество больше, чем в заявке,
    либо товара в заявке нет вовсе. Пустой список — выравнивание (уменьшением) возможно.
    """
    by_sku = {it.sku: it.quantity for it in content}
    agg: dict[int, int] = {}
    for box in opis.boxes:
        for p in box.products:
            sku = ozn_to_sku(p.ozn_sku)
            if sku is None:
                continue
            agg[sku] = agg.get(sku, 0) + p.quantity

    issues: list[str] = []
    for sku, q in sorted(agg.items()):
        cur = by_sku.get(sku)
        if cur is None:
            issues.append(f"OZN{sku}: есть в описи ({q} шт), но нет в заявке — добавить через Ozon нельзя")
        elif q > cur:
            issues.append(f"OZN{sku}: в описи {q} шт, в заявке {cur} — Ozon не даёт увеличить")
    return issues


def compare_opis_to_content(
    opis: Opis, content: list[SupplyContentItem]
) -> list[str]:
    """Сверить состав описи с составом поставки (= с оприходованием).

    Возвращает список расхождений (по ШК и количеству). Пустой список —
    опись полностью соответствует составу поставки.
    """
    # Опись: суммарное количество по каждому sku (один OZN может быть в разных коробах).
    opis_qty: dict[int, int] = {}
    issues: list[str] = []
    for box in opis.boxes:
        for p in box.products:
            sku = ozn_to_sku(p.ozn_sku)
            if sku is None:
                issues.append(f"{p.ozn_sku}: не распознан как OZN-код")
                continue
            opis_qty[sku] = opis_qty.get(sku, 0) + p.quantity

    bundle_qty = {it.sku: it.quantity for it in content}

    for sku in sorted(set(opis_qty) | set(bundle_qty)):
        o = opis_qty.get(sku)
        b = bundle_qty.get(sku)
        if o is None:
            issues.append(f"OZN{sku}: в оприходовании {b} шт, в описи отсутствует")
        elif b is None:
            issues.append(f"OZN{sku}: в описи {o} шт, в оприходовании отсутствует")
        elif o != b:
            issues.append(f"OZN{sku}: в описи {o} шт, в оприходовании {b} шт")
    return issues


def build_create_body(
    opis: Opis, content: list[SupplyContentItem], supply_id: int,
    delete_current_version: bool = True,
) -> tuple[dict, list[str]]:
    """Полное тело запроса cargoes/create + предупреждения."""
    cargoes, warnings = build_cargoes(opis, content)
    body = {
        "supply_id": supply_id,
        "cargoes": cargoes,
        "delete_current_version": delete_current_version,
    }
    return body, warnings
