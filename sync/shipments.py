"""Выкуп заказа Uzum -> Отгрузка (demand) МойСклад.

Отгрузка создаётся, когда все активные (неотменённые) позиции заказа
выкуплены (TO_WITHDRAW / dateIssued). Привязывается к заказу покупателя,
склад — «Uzum», дублирует доп. поля заказа.
"""
from __future__ import annotations

import logging

from config import PRICE_MINOR_UNITS
from moysklad.entities import STATE_ISSUED, meta_ref, ms_moment
from sync.context import SyncContext
from uzum.models import STATUS_TO_WITHDRAW

log = logging.getLogger(__name__)


def sync_shipments(ctx: SyncContext, dry_run: bool = False) -> None:
    for rec in ctx.db.orders_with_status("created"):
        try:
            _ship_order(ctx, rec, dry_run)
        except Exception:
            order_id = rec["uzum_order_id"]
            log.exception("Ошибка отгрузки заказа %s", order_id)
            ctx.notifier.send(f"⚠️ Uzum-sync: ошибка создания отгрузки по заказу {order_id}")


def _ship_order(ctx: SyncContext, rec: dict, dry_run: bool) -> None:
    order_id = rec["uzum_order_id"]
    items = [i for i in ctx.db.order_items(order_id) if (i["qty_synced"] or 0) > 0]
    if not items:
        return
    issued = [
        i for i in items
        if i["status"] == STATUS_TO_WITHDRAW
        or i["date_issued_ms"] is not None
        or (i["amount_returns"] or 0) > 0  # возврат возможен только после выкупа
    ]
    if len(issued) < len(items):
        return  # ждём выкупа всех активных позиций

    if dry_run:
        log.info("[dry-run] Создал бы отгрузку по заказу UZ-%s (%d позиций)", order_id, len(items))
        return

    positions = [
        {
            "assortment": {
                "meta": {
                    "href": i["ms_assortment_href"],
                    "type": i["ms_assortment_type"],
                    "mediaType": "application/json",
                }
            },
            "quantity": i["qty_synced"],
            "price": (i["price"] or 0) * PRICE_MINOR_UNITS,
        }
        for i in items
    ]
    issued_ms = max(i["date_issued_ms"] or 0 for i in items)
    payload = {
        "name": f"UZ-{order_id}",
        "externalCode": str(order_id),
        "organization": meta_ref(ctx.entities.organization()),
        "agent": meta_ref(ctx.entities.agent()),
        "store": meta_ref(ctx.entities.store_uzum()),
        "customerOrder": {
            "meta": {
                "href": rec["ms_order_href"],
                "type": "customerorder",
                "mediaType": "application/json",
            }
        },
        "positions": positions,
        "attributes": ctx.entities.attr_values(
            "demand",
            {
                "ID заказа Uzum": str(order_id),
                "Дата доставки": ms_moment(issued_ms) if issued_ms else None,
            },
        ),
    }
    if issued_ms:
        payload["moment"] = ms_moment(issued_ms)

    existing = ctx.ms.find_one("/entity/demand", f"externalCode={order_id}")
    demand = existing or ctx.ms.post("/entity/demand", payload)
    ctx.db.set_order_demand(order_id, demand["id"], demand["meta"]["href"])
    ctx.ms.put(
        f"/entity/customerorder/{rec['ms_order_id']}",
        {"state": ctx.entities.order_state_ref(STATE_ISSUED)},
    )
    # выкупленная дата покупателю — и на заказ
    if issued_ms:
        ctx.ms.put(
            f"/entity/customerorder/{rec['ms_order_id']}",
            {"attributes": ctx.entities.attr_values(
                "customerorder", {"Дата доставки": ms_moment(issued_ms)}
            )},
        )
    log.info("Создана отгрузка UZ-%s (заказ выкуплен)", order_id)
