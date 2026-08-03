"""Гибрид: открыть форму migfull с заполненными датами и номером поставки.

Использование: python migfull_open.py [НОМЕР_ПОСТАВКИ]
Без аргумента берёт первую подходящую заявку (для теста).
Менеджер дозаполняет выпадашки, жмёт «Создать», грузит Оприходование и закрывает окно.
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.disable(logging.CRITICAL)

from core.config import load_settings
from core.ozon_api import OzonClient, WORKABLE_STATES
from core.migfull_rpa import open_for_manager


def main() -> None:
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    with OzonClient(load_settings()) as c:
        orders = c.list_supply_orders(states=WORKABLE_STATES)
    if not orders:
        print("Активных заявок не найдено.")
        return
    if arg:
        order = next((o for o in orders if o.order_number == arg), None)
        if not order:
            print(f"Заявка {arg} не найдена среди активных.")
            return
    else:
        order = next((o for o in orders if o.is_crossdock and o.cluster_name), orders[0])

    data = {"date": order.timeslot_date, "ozon_number": order.order_number}
    print(f"Открываю migfull для поставки {order.order_number} "
          f"(кластер {order.cluster_name or '—'}, дата {data['date']}).")
    print("Заполняю даты и номер. Допишите выпадашки, создайте отгрузку, закройте окно.")
    open_for_manager(data)
    print("Окно закрыто.")


if __name__ == "__main__":
    main()
