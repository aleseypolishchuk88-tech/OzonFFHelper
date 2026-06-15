"""Полный цикл «опись → грузоместа → этикетки → ZIP с именами PAC».

Шаги:
  1. Собрать состав грузомест из описи и состава поставки (core.cargoes).
  2. Создать грузоместа (key = PAC-код) → operation_id → дождаться SUCCESS,
     получить маппинг PAC → cargo_id.
  3. Запросить этикетки на поставку → дождаться готовности → скачать PDF (N стр.).
  4. Разрезать PDF по страницам; на каждой странице найти cargo_id и переименовать
     страницу в соответствующий PAC-код. Упаковать в ZIP.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date

import httpx
from pypdf import PdfReader, PdfWriter

from core.cargoes import (
    build_cargoes,
    build_content_items,
    compare_opis_to_content,
    content_increase_issues,
)
from core.logger import get_logger
from core.models import Opis
from core.ozon_api import OzonAPIError, OzonClient

log = get_logger("ozonff.orchestrator")

_DONE_STATES = {"success", "completed", "done"}
_FAIL_STATES = {"error", "failed"}


@dataclass
class RunResult:
    zip_bytes: bytes | None = None
    zip_name: str = ""
    pac_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


def _poll(fn, *, attempts=20, delay=2.0, what="операции"):
    """Опрашивать статус-функцию до завершения. fn() -> dict со status."""
    last = {}
    for _ in range(attempts):
        last = fn()
        status = (last.get("status") or "").lower()
        if status in _DONE_STATES:
            return last
        if status in _FAIL_STATES:
            raise OzonAPIError(f"Ozon вернул статус {status} для {what}: {last}")
        time.sleep(delay)
    raise OzonAPIError(f"Истёк таймаут ожидания {what}: {last}")


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-zА-Яа-я0-9._-]+", "_", (s or "").strip()).strip("_")


def process(
    client: OzonClient,
    opis: Opis,
    *,
    warehouse_name: str = "",
    delete_current_version: bool = True,
    align_content: bool = True,
    progress=None,
) -> RunResult:
    """Прогнать опись через создание грузомест и сборку ZIP с этикетками.

    align_content=True: если состав заявки отличается от описи, сначала
    привести состав заявки в Ozon к описи (чтобы грузоместа = состав заявки).
    """
    res = RunResult()

    def step(msg: str):
        res.steps.append(msg)
        log.info(msg)
        if progress:
            progress(msg)

    supply_id = int(opis.supply_order_number)

    # 1. Состав поставки.
    step("Получаю состав поставки из Ozon…")
    order, content = client.get_order_content_by_number(opis.supply_order_number)
    if not warehouse_name:
        warehouse_name = order.warehouse_name

    # 2. При расхождении — привести состав заявки к описи (если позволяет роль ключа).
    if align_content and compare_opis_to_content(opis, content):
        increases = content_increase_issues(opis, content)
        if increases:
            # Ozon не разрешает увеличивать состав заявки — не дёргаем API впустую.
            res.warnings.append(
                "Состав заявки не выровнен: опись больше состава заявки, а Ozon не "
                "позволяет увеличивать. " + "; ".join(increases)
                + ". Поправьте состав заявки в ЛК Ozon или проверьте опись. "
                "Грузоместа созданы по описи."
            )
            step("⚠ Опись больше состава заявки — Ozon не даёт увеличить. Продолжаю без выравнивания.")
        else:
            step("Состав заявки отличается от описи — привожу заявку к описи…")
            try:
                op = client.update_supply_content(order.order_id, supply_id, build_content_items(opis))
                st = _poll(lambda: client.get_content_update_status(op), what="редактирования состава заявки")
                if st.get("errors"):
                    raise OzonAPIError(", ".join(map(str, st["errors"])))
                step("Состав заявки приведён к описи.")
            except OzonAPIError as e:
                res.warnings.append(
                    f"Не удалось автоматически выровнять состав заявки: {e}. "
                    "Грузоместа созданы по описи; если в ЛК Ozon будет несовпадение состава — "
                    "выровняйте состав заявки вручную (или используйте ключ с нужной ролью)."
                )
                step("⚠ Состав заявки выровнять не удалось — продолжаю без выравнивания.")

    cargoes, warns = build_cargoes(opis, content)
    res.warnings.extend(warns)

    # 2. Создание грузомест.
    step(f"Создаю {len(cargoes)} грузомест в заявке {supply_id}…")
    op_id = client.create_cargoes(supply_id, cargoes, delete_current_version=delete_current_version)
    info = _poll(lambda: client.get_cargoes_create_info(op_id), what="создания грузомест")
    mapping = {
        c["key"]: c["value"]["cargo_id"]
        for c in info.get("result", {}).get("cargoes", [])
    }
    pac_by_cargo = {str(cid): pac for pac, cid in mapping.items()}
    step(f"Грузоместа созданы: {', '.join(mapping)}")

    # 3. Запрос и скачивание этикеток (один PDF на всю поставку).
    step("Запрашиваю этикетки…")
    label_op = client.request_labels(supply_id)
    st = _poll(lambda: client.get_labels_status(label_op), what="генерации этикеток")
    result = st.get("result", st)
    file_url = result.get("file_url")
    file_guid = result.get("file_guid")
    step("Скачиваю PDF с этикетками…")
    if file_url:
        pdf_bytes = httpx.get(file_url, timeout=60).content
    elif file_guid:
        pdf_bytes = client.download_label_file(file_guid)
    else:
        raise OzonAPIError(f"В ответе этикеток нет file_url/file_guid: {st}")

    # 4. Разрезать PDF по страницам и переименовать в PAC по cargo_id со страницы.
    step("Режу PDF и переименовываю страницы в PAC-коды…")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_files: dict[str, bytes] = {}
    used_pac = set()
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").replace(" ", "").replace("\n", "")
        pac = next((p for cid, p in pac_by_cargo.items() if cid in text), None)
        name = f"{pac}.pdf" if pac else f"стр_{i + 1}.pdf"
        if pac:
            used_pac.add(pac)
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        page_files[name] = buf.getvalue()

    missing = set(mapping) - used_pac
    if missing:
        res.warnings.append(
            f"Не удалось сопоставить страницы для коробов: {', '.join(sorted(missing))}"
        )

    # 5. ZIP.
    zip_name = f"PAC_{date.today():%Y-%m-%d}_{_safe_name(warehouse_name)}_{supply_id}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in page_files.items():
            z.writestr(name, data)
    res.zip_bytes = buf.getvalue()
    res.zip_name = zip_name
    res.pac_files = sorted(page_files)
    step(f"Готово: {zip_name} ({len(page_files)} этикеток)")
    return res
