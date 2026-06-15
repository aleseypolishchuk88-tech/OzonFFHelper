"""Клиент Ozon Seller API.

Реализованы методы чтения (Шаг 2а):
  - validate_credentials  -> POST /v1/cluster/list (проверка ключей)
  - list_supply_orders    -> POST /v3/supply-order/list + /v3/supply-order/get
  - get_supply_orders     -> POST /v3/supply-order/get (детали по id)

Точные схемы подтверждены на живом API (июнь 2026). Пути можно переопределить
через атрибуты класса (на случай эволюции API).

Каждый запрос/ответ пишется в лог (api.log) с маскировкой Api-Key.
Сетевые сбои и 5xx — до 3 попыток с задержками 5/10/30 c.
"""
from __future__ import annotations

import json

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_chain,
    wait_fixed,
)

from core.config import Settings, mask_key
from core.logger import get_logger
from core.models import Supply, SupplyContentItem, SupplyOrder, SupplyWarehouse

log = get_logger("ozonff.api")

# Статусы заявок (фильтр /v3/supply-order/list). В API без префикса ORDER_STATE_.
STATE_DATA_FILLING = "DATA_FILLING"
STATE_READY_TO_SUPPLY = "READY_TO_SUPPLY"
# Статусы, в которых заявка ещё доступна для работы с грузоместами/составом.
WORKABLE_STATES = [STATE_DATA_FILLING, STATE_READY_TO_SUPPLY]


class OzonAPIError(Exception):
    """Ошибка обращения к Ozon API (валидация ключей, 4xx, неожиданный ответ)."""


class _RetryableError(Exception):
    """Временный сбой (сеть/5xx/429) — повторяем."""


# 3 попытки: паузы 5, 10, 30 c (после 1-й, 2-й, 3-й неудачи).
_retry = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_chain(wait_fixed(5), wait_fixed(10), wait_fixed(30)),
    retry=retry_if_exception_type(_RetryableError),
)


class OzonClient:
    # Пути эндпоинтов (переопределяемы при эволюции API).
    PATH_CLUSTER_LIST = "/v1/cluster/list"
    PATH_SUPPLY_LIST = "/v3/supply-order/list"
    PATH_SUPPLY_GET = "/v3/supply-order/get"
    PATH_SUPPLY_BUNDLE = "/v1/supply-order/bundle"
    PATH_CONTENT_UPDATE = "/v1/supply-order/content/update"
    PATH_CONTENT_UPDATE_STATUS = "/v1/supply-order/content/update/status"
    PATH_CARGOES_CREATE = "/v1/cargoes/create"
    PATH_CARGOES_CREATE_INFO = "/v2/cargoes/create/info"
    PATH_CARGOES_GET = "/v1/cargoes/get"
    PATH_CARGOES_DELETE = "/v1/cargoes/delete"
    PATH_LABEL_CREATE = "/v1/cargoes-label/create"
    PATH_LABEL_GET = "/v1/cargoes-label/get"
    PATH_LABEL_FILE = "/v1/cargoes-label/file/{file_guid}"

    def __init__(self, settings: Settings, timeout: float = 30.0):
        if not settings.is_configured:
            raise OzonAPIError("Не заданы Client-Id / Api-Key")
        self.settings = settings
        self._cluster_names: dict[int, str] | None = None
        self._client = httpx.Client(
            base_url=settings.api_base,
            headers={
                "Client-Id": settings.client_id,
                "Api-Key": settings.api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def __enter__(self) -> "OzonClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- низкоуровневый POST с логированием и retry ------------------------

    @_retry
    def _post(self, path: str, body: dict) -> dict:
        log.info("POST %s | Client-Id=%s Api-Key=%s | body=%s",
                 path, self.settings.client_id, mask_key(self.settings.api_key),
                 json.dumps(body, ensure_ascii=False))
        try:
            resp = self._client.post(path, json=body)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            log.warning("Сетевой сбой %s: %s — повтор", path, e)
            raise _RetryableError(str(e)) from e

        text = resp.text
        log.info("RESP %s [%s] %s", path, resp.status_code, text[:2000])

        if resp.status_code in (429,) or resp.status_code >= 500:
            raise _RetryableError(f"{resp.status_code} от {path}")
        if resp.status_code >= 400:
            raise OzonAPIError(f"Ozon API {resp.status_code} на {path}: {text[:500]}")
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise OzonAPIError(f"Некорректный JSON от {path}: {text[:300]}") from e

    # --- публичные методы --------------------------------------------------

    def validate_credentials(self) -> bool:
        """Проверить валидность ключей (запрос к /v1/cluster/list)."""
        self._post(self.PATH_CLUSTER_LIST, {"cluster_type": "CLUSTER_TYPE_OZON"})
        log.info("Ключи валидны")
        return True

    def get_cluster_names(self) -> dict[int, str]:
        """Карта {macrolocal_cluster_id: название кластера}. Кэшируется на клиенте."""
        if self._cluster_names is None:
            data = self._post(self.PATH_CLUSTER_LIST, {"cluster_type": "CLUSTER_TYPE_OZON"})
            names: dict[int, str] = {}
            for c in data.get("clusters") or []:
                mid = c.get("macrolocal_cluster_id")
                if mid is not None and c.get("name"):
                    names[int(mid)] = c["name"]
            self._cluster_names = names
        return self._cluster_names

    def list_supply_order_ids(
        self, states: list[str] | None = None, limit: int = 100
    ) -> list[int]:
        """Список id заявок в указанных статусах (по умолч. — заполнение данных)."""
        body = {
            "filter": {"states": states or [STATE_DATA_FILLING]},
            "limit": limit,
            "sort_by": 1,
        }
        data = self._post(self.PATH_SUPPLY_LIST, body)
        return list(data.get("order_ids") or [])

    def get_supply_orders(self, order_ids: list[int]) -> list[SupplyOrder]:
        """Детали заявок по id (батчами до 50)."""
        result: list[SupplyOrder] = []
        for i in range(0, len(order_ids), 50):
            chunk = order_ids[i : i + 50]
            data = self._post(self.PATH_SUPPLY_GET, {"order_ids": chunk})
            for o in data.get("orders") or []:
                result.append(self._parse_order(o))
        return result

    def list_supply_orders(
        self, states: list[str] | None = None, limit: int = 100
    ) -> list[SupplyOrder]:
        """Список заявок с деталями (list + get одним вызовом)."""
        ids = self.list_supply_order_ids(states=states, limit=limit)
        if not ids:
            return []
        orders = self.get_supply_orders(ids)
        # Подставить названия кластеров.
        try:
            names = self.get_cluster_names()
            for o in orders:
                if o.cluster_id is not None:
                    o.cluster_name = names.get(o.cluster_id)
        except OzonAPIError:
            pass  # без названий кластеров список всё равно работает
        return orders

    def get_supply_content(self, bundle_ids: list[str]) -> list[SupplyContentItem]:
        """Состав поставки(ок) по bundle_id (с пагинацией)."""
        items: list[SupplyContentItem] = []
        last_id = ""
        page_size = 100  # bundle: limit в диапазоне (0, 100]
        while True:
            body: dict = {"bundle_ids": bundle_ids, "limit": page_size}
            if last_id:
                body["last_id"] = last_id
            data = self._post(self.PATH_SUPPLY_BUNDLE, body)
            chunk = data.get("items") or []
            for it in chunk:
                items.append(
                    SupplyContentItem(
                        sku=it.get("sku"),
                        offer_id=it.get("offer_id"),
                        name=it.get("name"),
                        quantity=it.get("quantity") or 0,
                        barcode=it.get("barcode"),
                    )
                )
            last_id = data.get("last_id") or ""
            if not chunk or not last_id or len(chunk) < page_size:
                break
        return items

    def get_order_content_by_number(
        self, order_number: str, states: list[str] | None = None
    ) -> tuple[SupplyOrder, list[SupplyContentItem]]:
        """Найти заявку по номеру и вернуть её вместе с составом.

        По умолчанию ищет в статусах DATA_FILLING и READY_TO_SUPPLY (после
        создания грузомест заявка переходит в READY_TO_SUPPLY).
        """
        orders = self.list_supply_orders(states=states or WORKABLE_STATES)
        order = next((o for o in orders if o.order_number == str(order_number)), None)
        if order is None:
            raise OzonAPIError(f"Заявка №{order_number} не найдена среди активных")
        bundle_ids = [s.bundle_id for s in order.supplies if s.bundle_id]
        if not bundle_ids:
            raise OzonAPIError(f"У заявки №{order_number} нет bundle_id (состав недоступен)")
        return order, self.get_supply_content(bundle_ids)

    def update_supply_content(self, order_id: int, supply_id: int, items: list[dict]) -> str:
        """Заменить товарный состав заявки (ЗАПИСЬ). Возвращает operation_id."""
        body = {"order_id": order_id, "supply_id": supply_id, "items": items}
        data = self._post(self.PATH_CONTENT_UPDATE, body)
        errs = data.get("errors") or []
        if errs:
            raise OzonAPIError("Ozon отклонил новый состав: " + ", ".join(map(str, errs)))
        return data.get("operation_id", "")

    def get_content_update_status(self, operation_id: str) -> dict:
        """Статус редактирования состава по operation_id."""
        return self._post(self.PATH_CONTENT_UPDATE_STATUS, {"operation_id": operation_id})

    def create_cargoes(
        self, supply_id: int, cargoes: list[dict], delete_current_version: bool = True
    ) -> str:
        """Создать грузоместа (ЗАПИСЬ в ЛК). Возвращает operation_id."""
        body = {
            "supply_id": supply_id,
            "cargoes": cargoes,
            "delete_current_version": delete_current_version,
        }
        data = self._post(self.PATH_CARGOES_CREATE, body)
        return data.get("operation_id", "")

    def get_cargoes_create_info(self, operation_id: str) -> dict:
        """Статус передачи грузомест по operation_id."""
        return self._post(self.PATH_CARGOES_CREATE_INFO, {"operation_id": operation_id})

    def get_cargoes(self, supply_id: int) -> list[dict]:
        """Список созданных грузомест поставки: [{key, cargo_id, ...}]."""
        data = self._post(self.PATH_CARGOES_GET, {"supply_ids": [supply_id]})
        # структура ответа может быть {cargoes:[...]} или {result:[...]}
        return data.get("cargoes") or data.get("result") or []

    # --- этикетки грузомест (async) ----------------------------------------

    def request_labels(self, supply_id: int, cargo_ids: list[int] | None = None) -> str:
        """Запросить генерацию этикеток. Возвращает operation_id."""
        body: dict = {"supply_id": supply_id}
        if cargo_ids:
            body["cargo_ids"] = cargo_ids
        data = self._post(self.PATH_LABEL_CREATE, body)
        return data.get("operation_id", "")

    def get_labels_status(self, operation_id: str) -> dict:
        """Статус генерации этикеток + file_guid когда готово."""
        return self._post(self.PATH_LABEL_GET, {"operation_id": operation_id})

    def download_label_file(self, file_guid: str) -> bytes:
        """Скачать PDF с этикетками по file_guid."""
        path = self.PATH_LABEL_FILE.format(file_guid=file_guid)
        resp = self._client.get(path)
        if resp.status_code >= 400:
            raise OzonAPIError(f"Ozon API {resp.status_code} на {path}: {resp.text[:300]}")
        return resp.content

    @staticmethod
    def _parse_order(o: dict) -> SupplyOrder:
        wh_raw = o.get("drop_off_warehouse") or {}
        warehouse = SupplyWarehouse(
            warehouse_id=wh_raw.get("warehouse_id"),
            name=wh_raw.get("name"),
            address=wh_raw.get("address"),
        ) if wh_raw else None
        supplies = [
            Supply(
                supply_id=s.get("supply_id"),
                bundle_id=s.get("bundle_id"),
                state=s.get("state"),
                is_crossdock=s.get("is_crossdock"),
                macrolocal_cluster_id=(
                    int(s["macrolocal_cluster_id"])
                    if s.get("macrolocal_cluster_id") not in (None, "")
                    else None
                ),
            )
            for s in (o.get("supplies") or [])
            if s.get("supply_id")
        ]
        cluster_id = next((s.macrolocal_cluster_id for s in supplies if s.macrolocal_cluster_id), None)
        return SupplyOrder(
            order_id=o.get("order_id"),
            order_number=str(o.get("order_number") or ""),
            state=o.get("state") or "",
            warehouse=warehouse,
            supplies=supplies,
            created_date=o.get("created_date"),
            data_filling_deadline=o.get("data_filling_deadline"),
            cluster_id=cluster_id,
        )
