"""RPA-автоматизация ФФ-системы migfull через Playwright.

Сессия хранится в persistent-профиле (вход один раз): менеджер логинится
в открытом окне, cookie сохраняются в data/.mig_profile, дальше робот
работает в этой сессии.

Пока реализованы: вход (open_for_login) и открытие формы создания
(для отладки). Заполнение/создание добавляется по шагам.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "https://polishuk.migfull.app"
PROFILE_DIR = Path(__file__).resolve().parent.parent / "data" / ".mig_edge"

# Браузер робота. Edge (Chromium) есть на каждой Windows 11 и обычно не запущен,
# поэтому не конфликтует с открытым Chrome пользователя (в отличие от channel=chrome),
# и не требует качать ~150 МБ Chromium (который на этом ПК не скачивается).
BROWSER_CHANNEL = "msedge"


def _kill_edge() -> None:
    """Закрыть фоновые процессы Edge, чтобы избежать конфликта singleton при запуске.

    Менеджеры работают в Chrome; Edge обычно висит фоном — гасим его перед стартом робота.
    """
    try:
        subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"],
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _launch(pw, headless: bool = False):
    _kill_edge()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel=BROWSER_CHANNEL,
        headless=headless,
        viewport={"width": 1400, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"],
    )


def open_for_login() -> None:
    """Открыть окно migfull для входа. Блокирует, пока окно не закроют."""
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE_URL}/app", wait_until="domcontentloaded")
        print(">>> Войдите в migfull в открывшемся окне браузера.")
        print(">>> После входа просто ЗАКРОЙТЕ окно браузера — сессия сохранится.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass


def _native(page, field_id: str, value: str) -> None:
    page.select_option(f'[id="{field_id}"]', value)


def _date(page, field_id: str, value: str) -> None:
    if value:
        page.fill(f'[id="{field_id}"]', value)


def _custom(page, field_for: str, search_text: str) -> None:
    """Filament-выпадашка (Тип поставки/Площадка/Доставить до/Конечный пункт).

    Поле определяется по label[for=...]; триггер — button.fi-select-input-btn.
    Открываем → вводим текст для поиска → выбираем первый вариант (ArrowDown+Enter).
    """
    if not search_text:
        return
    field = page.locator(f'label[for="{field_for}"]').locator(
        'xpath=ancestor::div[contains(@class,"fi-fo-select-wrp")][1]'
    )
    btn = field.locator("button.fi-select-input-btn")
    btn.scroll_into_view_if_needed()
    btn.click()
    page.wait_for_timeout(500)
    page.keyboard.type(search_text, delay=20)
    page.wait_for_timeout(900)  # клиентский фильтр или серверный поиск
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)


def fill_create_form(page, d: dict) -> None:
    """Заполнить форму создания отгрузки данными d. НЕ отправляет."""
    page.goto(f"{BASE_URL}/app/shipments/create", wait_until="domcontentloaded")
    page.wait_for_selector('[id="form.number"]', timeout=20000)

    _date(page, "form.shipment_forecast", d.get("date", ""))
    _date(page, "form.time_slot", d.get("date", ""))
    _date(page, "form.marketplace_delivery_date", d.get("date", ""))

    if d.get("ozon_number"):
        page.fill('[id="form.number"]', str(d["ozon_number"]))

    if d.get("delivery_type"):
        _native(page, "form.filter_delivery_type", d["delivery_type"])

    _custom(page, "form.packaging_id", d.get("package_type", ""))
    _custom(page, "form.marketplace_id", d.get("marketplace", ""))
    if d.get("delivery_type") == "transit":
        _custom(page, "form.destination_to_id", d.get("intermediate", ""))
    _custom(page, "form.destination_marketplace_id", d.get("cluster", ""))


def fill_simple(page, d: dict) -> None:
    """Заполнить только надёжные поля (даты + номер поставки). Выпадашки — на менеджере."""
    page.goto(f"{BASE_URL}/app/shipments/create", wait_until="domcontentloaded")
    page.wait_for_selector('[id="form.number"]', timeout=20000)
    _date(page, "form.shipment_forecast", d.get("date", ""))
    _date(page, "form.time_slot", d.get("date", ""))
    _date(page, "form.marketplace_delivery_date", d.get("date", ""))
    if d.get("ozon_number"):
        page.fill('[id="form.number"]', str(d["ozon_number"]))


def open_for_manager(d: dict) -> None:
    """ГИБРИД: открыть форму в видимом окне, заполнить простое, оставить менеджеру.

    Блокирует, пока менеджер не закроет окно (он дозаполняет выпадашки и жмёт «Создать»).
    """
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        fill_simple(page, d)
        print(">>> Форма открыта и частично заполнена.")
        print(">>> Допишите выпадашки, нажмите «Создать», загрузите Оприходование, затем закройте окно.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass


def run_simple_test(d: dict, screenshot_path: str) -> str:
    """Проверка: заполнить простое в фоне, снять скриншот. НЕ отправляет."""
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            fill_simple(page, d)
            page.wait_for_timeout(400)
            page.screenshot(path=screenshot_path, full_page=True)
            return page.url
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def run_fill_test(d: dict, screenshot_path: str) -> str:
    """Открыть форму, заполнить, снять скриншот. НЕ отправляет. Вернёт URL."""
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            fill_create_form(page, d)
            page.wait_for_timeout(500)
            page.screenshot(path=screenshot_path, full_page=True)
            return page.url
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def check_login_and_form(screenshot_path: str | None = None) -> tuple[bool, str]:
    """Проверить, что сессия жива, открыть форму создания. Вернёт (logged_in, url)."""
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE_URL}/app/shipments/create", wait_until="networkidle")
        url = page.url
        logged = "/shipments/create" in url and "login" not in url.lower()
        if screenshot_path:
            page.screenshot(path=screenshot_path, full_page=True)
        try:
            ctx.close()
        except Exception:
            pass
        return logged, url
