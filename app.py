"""OzonFFHelper — Streamlit UI.

Две вкладки по бизнес-процессу:
  1. «Оприходование» — поставка из Ozon → её состав → XLSX для ФФ (отправка в ТГ).
  2. «Этикетки по описи» — опись от ФФ → грузоместа + этикетки (запись в Ozon — Шаг 2б, пока заглушка).
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import pandas as pd
import streamlit as st

from core import oprihodovanie as op
from core import orchestrator
from core.cargoes import compare_opis_to_content
from core.config import load_settings, mask_key, save_credentials
from core.opis_parser import OpisParseError, parse_opis
from core.ozon_api import WORKABLE_STATES, OzonAPIError, OzonClient

_STATE_RU = {"DATA_FILLING": "Заполнение данных", "READY_TO_SUPPLY": "Готова к поставке"}

st.set_page_config(page_title="OzonFFHelper", page_icon="📦", layout="wide")


# --- кэшируемые обращения к API --------------------------------------------


@st.cache_data(ttl=120, show_spinner="Загружаю заявки из Ozon…")
def fetch_orders(client_id: str, api_key: str, api_base: str):
    s = load_settings()
    s.client_id, s.api_key, s.api_base = client_id, api_key, api_base
    with OzonClient(s) as c:
        return c.list_supply_orders(states=WORKABLE_STATES)


@st.cache_data(ttl=120, show_spinner="Загружаю состав поставки…")
def fetch_content(client_id: str, api_key: str, api_base: str, order_number: str):
    s = load_settings()
    s.client_id, s.api_key, s.api_base = client_id, api_key, api_base
    with OzonClient(s) as c:
        _, items = c.get_order_content_by_number(order_number)
    return items


def order_label(o) -> str:
    state = _STATE_RU.get(o.state, o.state)
    return f"№{o.order_number} · {o.cluster_label} · {o.delivery_type_label} · {state}"


def is_ivanovo_okruzhnaya(o) -> bool:
    n = (o.warehouse_name or "").upper()
    return "ИВАНОВО" in n and "ОКРУЖНАЯ" in n


def my_orders(orders):
    """Свои поставки: Иваново-Окружная (кросс-док) + все прямые. Прячет чужой ФФ."""
    return [o for o in orders if (not o.is_crossdock) or is_ivanovo_okruzhnaya(o)]


# --- экран ввода ключей -----------------------------------------------------


def render_settings_form(settings):
    with st.form("creds"):
        st.subheader("🔑 Ключи Ozon Seller API")
        st.caption("Личный кабинет Ozon → Настройки → Seller API → Сгенерировать ключ")
        cid = st.text_input("Client-Id", value=settings.client_id)
        key = st.text_input("Api-Key", value=settings.api_key, type="password")
        submitted = st.form_submit_button("Проверить и сохранить", type="primary")
    if submitted:
        if not cid.strip() or not key.strip():
            st.error("Заполните оба поля.")
            return
        probe = load_settings()
        probe.client_id, probe.api_key = cid.strip(), key.strip()
        try:
            with OzonClient(probe) as c:
                c.validate_credentials()
        except OzonAPIError as e:
            st.error(f"Ключи не прошли проверку: {e}")
            return
        save_credentials(cid, key)
        fetch_orders.clear()
        st.success("Ключи проверены и сохранены.")
        st.rerun()


# --- общий выбор заявки -----------------------------------------------------


def select_order(orders, key: str):
    st.caption(f"Поставка — доступно {len(orders)}. Выберите из списка (список прокручивается):")
    with st.container(height=260, border=True):
        idx = st.radio(
            "Поставка",
            options=range(len(orders)),
            format_func=lambda i: order_label(orders[i]),
            key=key,
            label_visibility="collapsed",
        )
    return orders[idx]


# --- корзина (общая для обеих вкладок) --------------------------------------


def render_cart(orders):
    """Двухпанельный выбор поставок «в работу». Возвращает список выбранных."""
    st.session_state.setdefault("cart", [])
    numbers = {o.order_number for o in orders}
    # убрать из корзины то, чего уже нет в списке заявок
    st.session_state["cart"] = [n for n in st.session_state["cart"] if n in numbers]
    cart = st.session_state["cart"]
    by_num = {o.order_number: o for o in orders}
    available = [o for o in orders if o.order_number not in cart]
    cart_orders = [by_num[n] for n in cart]

    st.subheader("🧺 Поставки в работу")
    st.caption("Слева — все поставки. Добавь нужные направления в корзину (＋) — дальше всё идёт по корзине.")
    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown(f"**Доступные ({len(available)})**")
        with st.container(height=300, border=True):
            if not available:
                st.caption("Все поставки уже в корзине.")
            for o in available:
                c1, c2 = st.columns([6, 1])
                c1.write(order_label(o))
                if c2.button("＋", key=f"add_{o.order_number}", help="Добавить в корзину"):
                    cart.append(o.order_number)
                    st.rerun()

    with col_r:
        h1, h2 = st.columns([3, 1])
        h1.markdown(f"**В работе ({len(cart_orders)})**")
        if cart_orders and h2.button("Очистить", key="clear_cart"):
            cart.clear()
            st.rerun()
        with st.container(height=300, border=True):
            if not cart_orders:
                st.caption("Пусто. Добавь поставки слева кнопкой ＋.")
            for o in cart_orders:
                c1, c2 = st.columns([6, 1])
                c1.write(order_label(o))
                if c2.button("✕", key=f"rm_{o.order_number}", help="Убрать из корзины"):
                    cart.remove(o.order_number)
                    st.rerun()

    return cart_orders


# --- вкладка 1: Оприходование (батч по корзине) -----------------------------


def tab_oprihodovanie(settings, cart_orders):
    st.subheader("Листы «Оприходование» для корзины")
    if not cart_orders:
        st.info("Добавьте поставки в корзину выше — для них соберём оприходования.")
        return

    st.caption(f"В корзине {len(cart_orders)} поставок. Соберём по XLSX на каждую — одним архивом.")
    with st.expander("Параметры документа (общие для всех)", expanded=False):
        c1, c2, c3 = st.columns(3)
        doc_number = c1.text_input("Номер входящего документа", value="1")
        doc_date = c2.date_input("Дата входящего документа", value=date.today())
        price = c3.number_input("Цена, руб. (для всех)", value=op.DEFAULT_PRICE, step=100)
        counterparty = st.text_input("Поклажедатель", value=op.DEFAULT_COUNTERPARTY)
        inn = st.text_input("ИНН", value=op.DEFAULT_INN)
        note = st.text_area("Примечание (для всех строк)", value=op.DEFAULT_NOTE)

    if st.button("📋 Сформировать оприходования (ZIP)", type="primary"):
        with st.status("Собираю оприходования…", expanded=True) as status:
            try:
                files: dict[str, bytes] = {}
                with OzonClient(load_settings()) as client:
                    for o in cart_orders:
                        status.write(f"№{o.order_number} · {o.cluster_label}…")
                        bundle_ids = [s.bundle_id for s in o.supplies if s.bundle_id]
                        items = client.get_supply_content(bundle_ids)
                        xlsx = op.build_bytes(
                            items, doc_number=doc_number, doc_date=doc_date,
                            counterparty=counterparty, inn=inn, price=int(price), note=note,
                        )
                        fname = op.suggest_filename(o.order_number, o.cluster_name or o.warehouse_name)
                        files[fname] = xlsx
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for n, d in files.items():
                        z.writestr(n, d)
                status.update(label="Готово ✅", state="complete")
                st.session_state["oprih_zip"] = {
                    "name": f"Оприходования_{date.today():%Y-%m-%d}.zip",
                    "data": buf.getvalue(),
                    "count": len(files),
                }
            except Exception as e:  # noqa
                status.update(label="Ошибка", state="error")
                st.session_state.pop("oprih_zip", None)
                st.error(f"Не удалось: {e}")

    z = st.session_state.get("oprih_zip")
    if z:
        st.success(f"Готово: {z['count']} файлов оприходования.")
        st.download_button(
            f"⬇️ Скачать {z['name']}",
            data=z["data"], file_name=z["name"], mime="application/zip", type="primary",
        )


# --- вкладка 2: Этикетки по описи -------------------------------------------


def tab_labels(settings, cart_orders):
    st.subheader("Грузоместа и этикетки по описи от ФФ")
    if not cart_orders:
        st.info("Добавьте поставки в корзину выше — этикетки делаются по корзине.")
        return
    order = select_order(cart_orders, key="lbl_order")
    st.caption(
        f"Заявка №{order.order_number} · Кластер: {order.cluster_label} · "
        f"supply_id {', '.join(str(s.supply_id) for s in order.supplies)}"
    )

    uploaded = st.file_uploader("Загрузите XLSX-опись от ФФ", type=["xlsx"])
    if not uploaded:
        st.info("Загрузите файл описи, чтобы увидеть превью и сопоставление.")
        return

    try:
        opis = parse_opis(io.BytesIO(uploaded.getvalue()))
    except OpisParseError as e:
        st.error(f"Не удалось распарсить опись: {e}")
        return

    if opis.supply_order_number == order.order_number:
        st.success(f"✅ Опись соответствует заявке №{order.order_number}")
        matched = True
    else:
        auto = next((o for o in cart_orders if o.order_number == opis.supply_order_number), None)
        hint = f" Похоже, это заявка №{auto.order_number}." if auto else ""
        st.error(
            f"⚠ Номер в описи — {opis.supply_order_number}, а выбрана №{order.order_number}.{hint}"
        )
        matched = False

    m1, m2, m3 = st.columns(3)
    m1.metric("Коробов", opis.total_boxes)
    m2.metric("Единиц", opis.total_quantity)
    m3.metric("Опись №", opis.opis_number)

    rows = [
        {"PAC-код": b.pac_code, "OZN": p.ozn_sku, "Кол-во": p.quantity, "Вес, кг": b.weight_kg}
        for b in opis.boxes
        for p in b.products
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Сверка состава описи с составом поставки (= с оприходованием).
    can_run = matched
    if matched:
        try:
            items = fetch_content(
                settings.client_id, settings.api_key, settings.api_base, order.order_number
            )
            issues = compare_opis_to_content(opis, items)
        except OzonAPIError as e:
            issues = []
            st.warning(f"Не удалось сверить состав с Ozon: {e}")
        if issues:
            st.error("⚠ Состав описи не совпадает с составом заявки в Ozon:")
            for i in issues:
                st.markdown(f"- {i}")
            can_run = st.checkbox(
                "Подтверждаю: опись верна (состав меняли вручную). Привести заявку к описи и продолжить.",
                key="lbl_override",
            )
            if can_run:
                st.info(
                    "При запуске состав заявки в Ozon будет приведён к описи, "
                    "затем созданы грузоместа — состав заявки и грузомест совпадут (без доплаты)."
                )
        else:
            st.success("✅ Состав описи совпадает с составом заявки.")

    st.caption(
        "⚠ Кнопка создаёт грузоместа в Ozon (реальная запись в ЛК). "
        "Существующие грузоместа этой заявки будут заменены."
    )
    run = st.button(
        "Создать грузоместа и получить этикетки", type="primary", disabled=not can_run
    )

    if run:
        probe = load_settings()
        with st.status("Выполняю…", expanded=True) as status:
            try:
                with OzonClient(probe) as client:
                    res = orchestrator.process(
                        client, opis, warehouse_name=order.warehouse_name,
                        progress=lambda m: status.write(m),
                    )
                status.update(label="Готово ✅", state="complete")
                fetch_orders.clear()
                st.session_state["label_result"] = {
                    "name": res.zip_name,
                    "data": res.zip_bytes,
                    "files": res.pac_files,
                    "warnings": res.warnings,
                }
            except Exception as e:  # noqa
                status.update(label="Ошибка", state="error")
                st.session_state.pop("label_result", None)
                st.error(f"Не удалось: {e}")

    result = st.session_state.get("label_result")
    if result and result["data"]:
        for w in result["warnings"]:
            st.warning(w)
        st.success(f"Готово: {len(result['files'])} этикеток — {', '.join(result['files'])}")
        st.download_button(
            f"⬇️ Скачать {result['name']}",
            data=result["data"],
            file_name=result["name"],
            mime="application/zip",
            type="primary",
        )


# --- основной экран ---------------------------------------------------------


def render_main(settings):
    with st.sidebar:
        st.subheader("Подключение")
        st.write(f"**Client-Id:** `{settings.client_id}`")
        st.write(f"**Api-Key:** `{mask_key(settings.api_key)}`")
        if st.button("🔄 Обновить данные"):
            fetch_orders.clear()
            fetch_content.clear()
            st.rerun()
        st.divider()
        only_mine = st.checkbox(
            "Только Иваново-Окружная и прямые",
            value=True,
            help="Скрыть кросс-док поставки другого ФФ (например, питерского).",
        )
        with st.expander("Сменить ключи"):
            render_settings_form(settings)

    st.title("📦 OzonFFHelper")

    try:
        orders = fetch_orders(settings.client_id, settings.api_key, settings.api_base)
    except OzonAPIError as e:
        st.error(f"Не удалось получить заявки: {e}")
        return
    if only_mine:
        orders = my_orders(orders)
    if not orders:
        st.warning("Активных заявок не найдено (проверьте фильтр в боковой панели).")
        return

    cart_orders = render_cart(orders)
    st.divider()

    t1, t2 = st.tabs(["📋 Оприходование", "🏷 Этикетки по описи"])
    with t1:
        tab_oprihodovanie(settings, cart_orders)
    with t2:
        tab_labels(settings, cart_orders)


# --- точка входа ------------------------------------------------------------

settings = load_settings()
if not settings.is_configured:
    st.title("📦 OzonFFHelper")
    st.write("Первый запуск — введите ключи Ozon Seller API.")
    render_settings_form(settings)
else:
    render_main(settings)
