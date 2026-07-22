"""Возврат после выкупа -> Возврат покупателя (salesreturn) на склад «Uzum».

Отслеживается по amountReturns в позициях finance/orders (снапшот кладёт
sync_orders в SQLite): дельта против returns_synced оформляется возвратом
на основании отгрузки.
"""
from __future__ import annotations

import logging

from config import PRICE_MINOR_UNITS
from moysklad.entities import STATE_RETURNED, meta_ref
from sync.context import SyncContext

log = logging.getLogger(__name__)


def sync_returns(ctx: SyncContext, dry_run: bool = False) -> None:
    for rec in ctx.db.orders_with_status("shipped", "returned"):
        order_id = rec["uzum_order_id"]
        try:
            _return_order_delta(ctx, rec, order_id, ctx.db.order_items(order_id), dry_run)
        except Exception:
            log.exception("Ошибка возврата по заказу %s", order_id)
            ctx.notifier.send(f"⚠️ Uzum-sync: ошибка создания возврата по заказу {order_id}")


def _return_order_delta(
    ctx: SyncContext, rec: dict, order_id: str, items: list[dict], dry_run: bool
) -> None:
    deltas = [
        (i, (i["amount_returns"] or 0) - (i["returns_synced"] or 0)) for i in items
    ]
    deltas = [(i, d) for i, d in deltas if d > 0]

    # позиция, не попавшая в отгрузку, со склада не уходила — возврат в МС
    # не нужен, просто закрываем дельту, чтобы она не висела вечно
    shippable = []
    for i, d in deltas:
        if not i["ms_assortment_href"] or (i["qty_synced"] or 0) <= 0:
            log.info(
                "Возврат UZ-%s: позиция «%s» не была отгружена — закрыт без документа",
                order_id, i["sku_title"],
            )
            if not dry_run:
                ctx.db.set_item_returns(i["uzum_item_id"], i["amount_returns"] or 0)
            continue
        shippable.append((i, d))
    deltas = shippable
    if not deltas:
        return
    if rec["ms_demand_href"] is None:
        log.warning("Возврат по заказу UZ-%s: отгрузка ещё не создана, отложено", order_id)
        return

    if dry_run:
        log.info("[dry-run] Создал бы возврат по заказу UZ-%s (%d позиций)", order_id, len(deltas))
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
            "quantity": delta,
            "price": (i["price"] or 0) * PRICE_MINOR_UNITS,
        }
        for i, delta in deltas
    ]
    causes = {i["return_cause"] for i, _ in deltas if i["return_cause"]}
    payload = {
        "organization": meta_ref(ctx.entities.organization()),
        "agent": meta_ref(ctx.entities.agent()),
        "store": meta_ref(ctx.entities.store_uzum()),
        "demand": {
            "meta": {
                "href": rec["ms_demand_href"],
                "type": "demand",
                "mediaType": "application/json",
            }
        },
        "description": f"Возврат Uzum по заказу {order_id}"
        + (f". Причина: {'; '.join(sorted(causes))}" if causes else ""),
        "positions": positions,
    }
    ctx.ms.post("/entity/salesreturn", payload)
    for i, delta in deltas:
        ctx.db.set_item_returns(i["uzum_item_id"], (i["returns_synced"] or 0) + delta)
    ctx.ms.put(
        f"/entity/customerorder/{rec['ms_order_id']}",
        {"state": ctx.entities.order_state_ref(STATE_RETURNED)},
    )
    ctx.db.set_order_status(order_id, "returned")
    log.info("Создан возврат покупателя по заказу UZ-%s", order_id)
