"""Внешние коды, названия и описания документов МойСклад.

Стиль "uzum-supply" повторяет соглашения серверной синхронизации основного
аккаунта: по внешнему коду обе программы видят документы друг друга и не
создают дублей. Названия в этом стиле не задаются — нумерует МойСклад.
"""
from __future__ import annotations

from config import Config


class Naming:
    def __init__(self, cfg: Config) -> None:
        self._compat = cfg.compat
        self._label = f", магазин {cfg.shop_label}" if cfg.shop_label else ""

    def order_ext(self, order_id) -> str:
        return f"uzum-o-{order_id}" if self._compat else str(order_id)

    def order_name(self, order_id) -> str | None:
        return None if self._compat else f"UZ-{order_id}"

    def order_description(self, order_id) -> str | None:
        return f"Заказ Uzum {order_id}{self._label}" if self._compat else None

    def demand_ext(self, order_id) -> str:
        return f"uzum-d-{order_id}" if self._compat else str(order_id)

    def demand_name(self, order_id) -> str | None:
        return None if self._compat else f"UZ-{order_id}"

    def return_ext(self, order_id, returned_before: int, total_after: int) -> str:
        if self._compat:
            # первый возврат по заказу — как у серверной синхронизации
            return f"uzum-r-{order_id}" if returned_before == 0 else f"uzum-r-{order_id}-{total_after}"
        return f"ret-{order_id}-{total_after}"

    def move_ext(self, invoice_id) -> str:
        return f"uzum-inv-{invoice_id}" if self._compat else str(invoice_id)

    def move_name(self, invoice_number) -> str | None:
        return None if self._compat else f"UZ-INV-{invoice_number}"

    def move_description(self, invoice_number, invoice_id) -> str:
        return f"Накладная Uzum {invoice_number} (id {invoice_id}){self._label}"

    def loss_ext(self, invoice_id) -> str:
        return f"uzum-inv-loss-{invoice_id}" if self._compat else f"loss-{invoice_id}"

    def loss_name(self, invoice_number) -> str | None:
        return None if self._compat else f"UZ-LOSS-{invoice_number}"


def put_name(payload: dict, name: str | None) -> dict:
    if name:
        payload["name"] = name
    return payload
