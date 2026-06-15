"""Pydantic-модели предметной области OzonFFHelper.

Модель описи (Opis) — результат парсинга XLSX от фулфилмент-компании.
Состав: опись -> коробки (Box, она же PAC-грузоместо) -> товары (Product, OZN-артикулы).
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Product(BaseModel):
    """Товар внутри короба — одна OZN-строка описи."""

    ozn_sku: str = Field(..., description="Артикул вида OZN891162680")
    name: str | None = Field(None, description="Номенклатура (наименование)")
    characteristic: str | None = Field(None, description="Характеристика: цвет/форма")
    quantity: int = Field(..., ge=1, description="Количество единиц в коробе")


class Box(BaseModel):
    """Короб (грузоместо) — одна PAC-строка описи и привязанные к ней OZN-строки."""

    pac_code: str = Field(..., description="Код короба вида PAC200551831")
    products: list[Product] = Field(default_factory=list)
    weight_kg: float | None = Field(None, description="Масса короба, кг")
    volume_m3: float | None = Field(None, description="Объём короба, м³")

    @property
    def total_quantity(self) -> int:
        return sum(p.quantity for p in self.products)


class Opis(BaseModel):
    """Опись к реализации целиком."""

    opis_number: str = Field(..., description="Номер описи, напр. 62089")
    opis_date: date = Field(..., description="Дата описи")
    counterparty: str | None = Field(None, description="Контрагент (ФИО ИП)")
    warehouse_ozon: str | None = Field(
        None, description='Склад Ozon, напр. "Озон | ИВАНОВО СЦ Окружная"'
    )
    warehouse_mp: str | None = Field(None, description="Адрес склада МП")
    supply_order_number: str = Field(
        ..., description="Номер поставки Ozon — ключ для сопоставления с заявкой"
    )
    upd_number: str | None = Field(None, description="Номер входящего документа (УПД)")
    boxes: list[Box] = Field(default_factory=list)

    @property
    def total_boxes(self) -> int:
        return len(self.boxes)

    @property
    def total_quantity(self) -> int:
        return sum(b.total_quantity for b in self.boxes)


# --- модели заявки на поставку Ozon (ответ Seller API) ----------------------


class SupplyWarehouse(BaseModel):
    """Склад назначения поставки (drop_off_warehouse)."""

    warehouse_id: int | None = None
    name: str | None = None
    address: str | None = None


class Supply(BaseModel):
    """Поставка внутри заявки. cargoes создаются по supply_id."""

    supply_id: int
    bundle_id: str | None = None
    state: str | None = None
    is_crossdock: bool | None = None
    macrolocal_cluster_id: int | None = None


class SupplyContentItem(BaseModel):
    """Товар в составе поставки (ответ /v1/supply-order/bundle)."""

    sku: int
    offer_id: str | None = None
    name: str | None = None
    quantity: int = 0
    barcode: str | None = None

    @property
    def ozn_code(self) -> str:
        """ШК для ФФ — OZN-код. Совпадает с 'OZN'+sku (как в описи)."""
        return f"OZN{self.sku}"


class SupplyOrder(BaseModel):
    """Заявка на поставку FBO (ответ /v3/supply-order/get)."""

    order_id: int
    order_number: str = ""  # напр. 2000055939827 — ключ сопоставления с описью
    state: str = ""
    warehouse: SupplyWarehouse | None = None
    supplies: list[Supply] = Field(default_factory=list)
    created_date: str | None = None
    data_filling_deadline: str | None = None
    cluster_id: int | None = None
    cluster_name: str | None = None

    @property
    def warehouse_name(self) -> str:
        return self.warehouse.name if self.warehouse and self.warehouse.name else "—"

    @property
    def cluster_label(self) -> str:
        return self.cluster_name or (f"кластер {self.cluster_id}" if self.cluster_id else "—")

    @property
    def is_crossdock(self) -> bool:
        """True — кросс-док (Ozon развозит по кластерам); False — прямая поставка."""
        return any(s.is_crossdock for s in self.supplies)

    @property
    def delivery_type_label(self) -> str:
        return "кросс-док" if self.is_crossdock else "прямая"
