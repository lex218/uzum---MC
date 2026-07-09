"""Заказ Uzum -> Заказ покупателя МойСклад.

Строки /v1/finance/orders группируются по orderId в один заказ покупателя.
Идемпотентность: SQLite (orders/order_items) + externalCode в МойСклад.
Отмены: позиции уменьшаются; полностью отменённый заказ переводится в
статус «Uzum: отменён» и распроводится (снимается резерв).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from config import PRICE_MINOR_UNITS
from moysklad.entities import (
    STATE_CANCELLED,
    STATE_PROCESSING,
    meta_ref,
    ms_moment,
)
from sync.context import SyncContext
from uzum.models import OrderItem

log = logging.getLogger(__name__)

KV_LAST_POLL = "orders_last_poll_s"
KV_INITIAL_START = "orders_initial_start_s"


def _poll_window(ctx: SyncContext, now_s: int) -> int:
    """Нижняя граница окна опроса: перекрытие + самый старый открытый заказ.

    Ниже точки первого запуска (KV_INITIAL_START) окно не опускается,
    чтобы перекрытие не затягивало историю; исключение — открытые
    заказы, которые уже отслеживаются.
    """
    start = ctx.db.get_kv(KV_INITIAL_START)
    if start is None:
        start = str(now_s - ctx.cfg.orders_initial_days * 86400)
    last = ctx.db.get_kv(KV_LAST_POLL)
    if last is None:
        date_from = int(start)
    else:
        date_from = max(int(last) - ctx.cfg.orders_overlap_hours * 3600, int(start))
    oldest_ms = ctx.db.oldest_open_order_ms()
    if oldest_ms is not None:
        date_from = min(date_from, oldest_ms // 1000 - 3600)
    return date_from


def sync_orders(ctx: SyncContext, dry_run: bool = False) -> None:
    now_s = int(time.time())
    date_from = _poll_window(ctx, now_s)
    log.info("Опрос заказов Uzum с %s", time.strftime("%Y-%m-%d %H:%M", time.localtime(date_from)))

    grouped: dict[int, list[OrderItem]] = defaultdict(list)
    for item in ctx.uzum.finance_orders(ctx.shop_id, date_from_s=date_from, date_to_s=now_s):
        grouped[item.order_id].append(item)

    log.info("Получено заказов: %d", len(grouped))
    for order_id, items in grouped.items():
        try:
            _process_order(ctx, order_id, items, dry_run)
        except Exception:
            log.exception("Ошибка обработки заказа %s", order_id)
            ctx.notifier.send(f"⚠️ Uzum-sync: ошибка обработки заказа {order_id}, см. лог")

    if not dry_run:
        ctx.db.set_kv(KV_LAST_POLL, str(now_s))
        if ctx.db.get_kv(KV_INITIAL_START) is None:
            ctx.db.set_kv(KV_INITIAL_START, str(date_from))


def _save_items(ctx: SyncContext, order_id: int, items: list[OrderItem], resolved: dict[int, dict]) -> None:
    for it in items:
        found = resolved.get(it.id)
        ctx.db.upsert_item(
            it.id, order_id, it.sku_title,
            found["href"] if found else None,
            found["type"] if found else None,
            it.sell_price, it.effective_qty, it.status, it.date_issued_ms,
            amount_returns=it.amount_returns, return_cause=it.return_cause,
        )


def _process_order(ctx: SyncContext, order_id: int, items: list[OrderItem], dry_run: bool) -> None:
    rec = ctx.db.get_order(order_id)
    if rec is None:
        _create_order(ctx, order_id, items, dry_run)
    else:
        _update_order(ctx, rec, order_id, items, dry_run)


def _create_order(ctx: SyncContext, order_id: int, items: list[OrderItem], dry_run: bool) -> None:
    active = [i for i in items if i.effective_qty > 0]
    if not active:
        # заказ отменён целиком до того, как мы его увидели — в МС не создаём
        if not dry_run:
            ctx.db.upsert_order(order_id, "skipped", first_seen_ms=items[0].date_ms)
        log.info("Заказ %s полностью отменён до синхронизации — пропуск", order_id)
        return

    resolved: dict[int, dict] = {}
    for it in active:
        found = ctx.resolver.resolve(it.sku_title, product_id=it.product_id)
        if found is None:
            ctx.notifier.send(
                f"❌ Uzum-sync: заказ {order_id} не создан — SKU «{it.sku_title}» "
                f"({it.product_title}) не найден в МойСклад. Заведите карточку "
                f"(штрихкод Uzum или артикул) — заказ подтянется следующим циклом."
            )
            return
        resolved[it.id] = found

    positions = [
        {
            "assortment": {"meta": _meta_for(resolved[it.id])},
            "quantity": it.effective_qty,
            "price": it.sell_price * PRICE_MINOR_UNITS,
            "reserve": it.effective_qty,
        }
        for it in active
    ]
    payload = {
        "name": f"UZ-{order_id}",
        "externalCode": str(order_id),
        "moment": ms_moment(min(i.date_ms for i in items)),
        "organization": meta_ref(ctx.entities.organization()),
        "agent": meta_ref(ctx.entities.agent()),
        "store": meta_ref(ctx.entities.store_uzum()),
        "state": ctx.entities.order_state_ref(STATE_PROCESSING),
        "positions": positions,
        "attributes": ctx.entities.attr_values(
            "customerorder",
            {
                "ID заказа Uzum": str(order_id),
                "Комиссия": float(sum(i.commission for i in active)),
                "Логистика": float(sum(i.logistic_fee for i in active)),
            },
        ),
    }

    if dry_run:
        log.info("[dry-run] Создал бы заказ UZ-%s: %d позиций", order_id, len(positions))
        return

    # страховка от дублей, если SQLite потеряна: ищем по externalCode
    existing = ctx.ms.find_one("/entity/customerorder", f"externalCode={order_id}")
    created = existing or ctx.ms.post("/entity/customerorder", payload)
    ctx.db.upsert_order(
        order_id, "created",
        ms_order_id=created["id"], ms_order_href=created["meta"]["href"],
        first_seen_ms=min(i.date_ms for i in items),
    )
    _save_items(ctx, order_id, items, resolved)
    log.info("Создан заказ покупателя UZ-%s (%d позиций)", order_id, len(positions))


def _update_order(ctx: SyncContext, rec: dict, order_id: int, items: list[OrderItem], dry_run: bool) -> None:
    if rec["ms_order_id"] is None:  # skipped
        return

    # обновляем снапшот позиций (статусы, выкуп, возвраты) для shipments/returns
    known = {i["uzum_item_id"]: i for i in ctx.db.order_items(order_id)}
    decreases: list[tuple[OrderItem, dict]] = []
    for it in items:
        db_item = known.get(str(it.id))
        if db_item is None:
            continue
        if it.effective_qty < (db_item["qty_synced"] or 0):
            decreases.append((it, db_item))
        if not dry_run:
            ctx.db.upsert_item(
                it.id, order_id, it.sku_title,
                db_item["ms_assortment_href"], db_item["ms_assortment_type"],
                it.sell_price, db_item["qty_synced"], it.status, it.date_issued_ms,
                amount_returns=it.amount_returns, return_cause=it.return_cause,
            )

    all_cancelled = all(i.effective_qty == 0 for i in items)
    if rec["status"] in ("cancelled",):
        return

    if all_cancelled and rec["status"] == "created":
        if dry_run:
            log.info("[dry-run] Отменил бы заказ UZ-%s", order_id)
            return
        ctx.ms.put(
            f"/entity/customerorder/{rec['ms_order_id']}",
            {"state": ctx.entities.order_state_ref(STATE_CANCELLED), "applicable": False},
        )
        ctx.db.set_order_status(order_id, "cancelled")
        for it in items:
            ctx.db.set_item_qty(it.id, 0)
        log.info("Заказ UZ-%s отменён (все позиции отменены в Uzum)", order_id)
        return

    if decreases and rec["status"] == "created":
        _apply_decreases(ctx, rec, order_id, decreases, dry_run)


def _apply_decreases(
    ctx: SyncContext, rec: dict, order_id: int,
    decreases: list[tuple[OrderItem, dict]], dry_run: bool,
) -> None:
    """Частичная отмена: уменьшить/удалить позиции заказа в МС."""
    if dry_run:
        log.info("[dry-run] Уменьшил бы %d позиций заказа UZ-%s", len(decreases), order_id)
        return
    positions = ctx.ms.rows(f"/entity/customerorder/{rec['ms_order_id']}/positions")
    by_href = {p["assortment"]["meta"]["href"]: p for p in positions}
    for it, db_item in decreases:
        pos = by_href.get(db_item["ms_assortment_href"])
        if pos is None:
            log.warning("Позиция «%s» заказа UZ-%s не найдена в МС", it.sku_title, order_id)
            continue
        pos_id = pos["id"]
        new_qty = it.effective_qty
        if new_qty <= 0:
            ctx.ms.delete(f"/entity/customerorder/{rec['ms_order_id']}/positions/{pos_id}")
        else:
            ctx.ms.put(
                f"/entity/customerorder/{rec['ms_order_id']}/positions/{pos_id}",
                {"quantity": new_qty, "reserve": new_qty},
            )
        ctx.db.set_item_qty(it.id, new_qty)
        log.info(
            "Заказ UZ-%s: позиция «%s» %s -> %s (отмена в Uzum)",
            order_id, it.sku_title, db_item["qty_synced"], new_qty,
        )


def _meta_for(found: dict) -> dict:
    return {"href": found["href"], "type": found["type"], "mediaType": "application/json"}
