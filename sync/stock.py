"""Сверка и выравнивание остатков склада «Uzum» с данными Uzum API.

Uzum периодически теряет и находит товары на складе, поэтому остатки
приводятся к данным Uzum корректирующими документами:
  недостача (в МС больше, чем в Uzum)  -> Списание (loss)
  излишек   (в Uzum больше, чем в МС)  -> Оприходование (enter)

Сравнивается quantityActive Uzum со СВОБОДНЫМ остатком МойСклад
(физический минус резерв, stockType=freeStock): открытые заказы
зарезервированы в МС и уже исключены из активного остатка Uzum.

Автокоррекция (STOCK_AUTOCORRECT=1) применяется только к расхождениям,
стабильным две сверки подряд, — свежие расхождения могут быть эффектом
документов в пути. Команда align-stock выравнивает немедленно
(первичная загрузка остатков).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from config import PRICE_MINOR_UNITS
from moysklad.entities import meta_ref
from sync.context import SyncContext

log = logging.getLogger(__name__)

KV_PREV_DIFFS = "stock_prev_diffs"
MAX_LINES = 30
MAX_POSITIONS = 900  # лимит МойСклад — 1000 элементов массива на запрос


@dataclass
class StockDiff:
    assortment_id: str
    href: str
    type: str
    title: str  # skuTitle (или несколько через запятую)
    uzum_qty: int
    ms_qty: int
    purchase_price: int  # сумы, для себестоимости оприходования

    @property
    def delta(self) -> int:
        return self.uzum_qty - self.ms_qty


def _collect_diffs(ctx: SyncContext) -> list[StockDiff]:
    store_href = ctx.entities.store_uzum()["meta"]["href"]
    store_id = store_href.rstrip("/").rsplit("/", 1)[-1]

    # имя поля с количеством совпадает с запрошенным stockType
    rows = ctx.ms.get("/report/stock/bystore/current", {"stockType": "freeStock"}) or []
    ms_stock = {
        r["assortmentId"]: r["freeStock"] for r in rows if r.get("storeId") == store_id
    }

    # товар «в пути»: перемещение уже создано, но приёмка на складе Uzum не
    # завершена — в МС остаток уже есть, в Uzum ещё нет; исключаем из сверки
    in_transit = ctx.db.pending_invoice_sent()

    # агрегируем по карточке МС: несколько SKU Uzum могут вести на одну позицию
    agg: dict[str, StockDiff] = {}
    for sku in ctx.resolver.catalog_skus():
        found = ctx.resolver.resolve(sku.sku_title, product_id=sku.product_id, sku_id=sku.sku_id)
        if found is None:
            continue  # о несматченных SKU сообщают модули документов
        aid = found["href"].rstrip("/").rsplit("/", 1)[-1]
        transit_qty = in_transit.get(str(sku.sku_id), 0)
        if aid in agg:
            agg[aid].uzum_qty += sku.quantity_active + transit_qty
            agg[aid].title += f", {sku.sku_title}"
        else:
            agg[aid] = StockDiff(
                assortment_id=aid,
                href=found["href"],
                type=found["type"],
                title=sku.sku_title,
                uzum_qty=sku.quantity_active + transit_qty,
                ms_qty=int(ms_stock.get(aid, 0)),
                purchase_price=sku.purchase_price,
            )
    return [d for d in agg.values() if d.delta != 0]


def _position(d: StockDiff, qty: int, with_price: bool) -> dict:
    pos = {
        "assortment": {
            "meta": {"href": d.href, "type": d.type, "mediaType": "application/json"}
        },
        "quantity": qty,
    }
    if with_price and d.purchase_price:
        pos["price"] = d.purchase_price * PRICE_MINOR_UNITS
    return pos


def _diff_key(diffs: list[StockDiff]) -> str:
    """Детерминированный ключ набора расхождений (идемпотентность коррекций)."""
    raw = "|".join(f"{d.assortment_id}:{d.delta}" for d in sorted(diffs, key=lambda d: d.assortment_id))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _post_chunked(ctx: SyncContext, entity: str, base_code: str, payload_base: dict,
                  positions: list[dict]) -> None:
    """POST документа частями по MAX_POSITIONS с externalCode-защитой от дублей."""
    for n, start in enumerate(range(0, len(positions), MAX_POSITIONS)):
        code = f"{base_code}-{n}"
        if ctx.ms.find_one(f"/entity/{entity}", f"externalCode={code}"):
            continue  # уже создан прошлой (упавшей) попыткой
        ctx.ms.post(
            f"/entity/{entity}",
            {**payload_base, "externalCode": code,
             "positions": positions[start:start + MAX_POSITIONS]},
        )


def _correct(ctx: SyncContext, diffs: list[StockDiff], reason: str) -> None:
    """Создать оприходование (нашлись) и/или списание (потерялись)."""
    found = [d for d in diffs if d.delta > 0]
    lost = [d for d in diffs if d.delta < 0]
    store = meta_ref(ctx.entities.store_uzum())
    org = meta_ref(ctx.entities.organization())
    key = _diff_key(diffs)

    def _lines(items: list[StockDiff]) -> str:
        return "; ".join(
            f"«{d.title}»: Uzum {d.uzum_qty}, МойСклад {d.ms_qty}" for d in items
        )

    if found:
        _post_chunked(
            ctx, "enter", f"stockfix-{key}-in",
            {
                "organization": org,
                "store": store,
                "description": (f"Корректировка остатков по данным Uzum ({reason}): "
                                f"товар найден/довезён. {_lines(found)}")[:4000],
            },
            [_position(d, d.delta, with_price=True) for d in found],
        )
        log.info("Оприходование: %d позиций (+%d шт)", len(found), sum(d.delta for d in found))
    if lost:
        _post_chunked(
            ctx, "loss", f"stockfix-{key}-out",
            {
                "organization": org,
                "store": store,
                "description": (f"Корректировка остатков по данным Uzum ({reason}): "
                                f"недостача на складе. {_lines(lost)}")[:4000],
            },
            [_position(d, -d.delta, with_price=False) for d in lost],
        )
        log.info("Списание: %d позиций (-%d шт)", len(lost), -sum(d.delta for d in lost))

    lines = [f"«{d.title}»: {'+' if d.delta > 0 else ''}{d.delta} шт" for d in diffs]
    shown = lines[:MAX_LINES]
    more = f"\n… и ещё {len(lines) - MAX_LINES}" if len(lines) > MAX_LINES else ""
    ctx.notifier.send(
        f"🔧 Uzum-sync: остатки склада «Uzum» скорректированы ({reason}), "
        f"{len(diffs)} позиций:\n" + "\n".join(shown) + more
    )


def check_stock(ctx: SyncContext, dry_run: bool = False) -> list[StockDiff]:
    """Плановая сверка: автокоррекция стабильных расхождений (2 сверки подряд)."""
    diffs = _collect_diffs(ctx)
    if not diffs:
        log.info("Остатки склада «Uzum» сходятся с Uzum API")
        if not dry_run:
            ctx.db.set_kv(KV_PREV_DIFFS, "{}")
        return []

    prev: dict[str, int] = json.loads(ctx.db.get_kv(KV_PREV_DIFFS) or "{}")
    stable = [d for d in diffs if prev.get(d.assortment_id) == d.delta]
    fresh = [d for d in diffs if prev.get(d.assortment_id) != d.delta]

    if dry_run:
        log.info(
            "[dry-run] Расхождений: %d (стабильных: %d) — коррекция не выполняется",
            len(diffs), len(stable),
        )
        return diffs

    if stable and ctx.cfg.stock_autocorrect:
        _correct(ctx, stable, reason="стабильное расхождение двух сверок")
        # скорректированные исчезнут из следующей сверки; в снапшот — только свежие
        ctx.db.set_kv(KV_PREV_DIFFS, json.dumps({d.assortment_id: d.delta for d in fresh}))
    else:
        ctx.db.set_kv(KV_PREV_DIFFS, json.dumps({d.assortment_id: d.delta for d in diffs}))
        if stable and not ctx.cfg.stock_autocorrect:
            lines = [f"«{d.title}»: Uzum {d.uzum_qty}, МойСклад {d.ms_qty}" for d in stable]
            ctx.notifier.send(
                f"📊 Uzum-sync: стабильные расхождения остатков склада «Uzum» "
                f"({len(stable)} SKU, автокоррекция выключена):\n"
                + "\n".join(lines[:MAX_LINES])
            )
    if fresh:
        log.info("Свежих расхождений: %d — ждём подтверждения следующей сверкой", len(fresh))
    return diffs


def align_stock(ctx: SyncContext, dry_run: bool = False) -> list[StockDiff]:
    """Немедленное выравнивание остатков (первичная загрузка / ручной запуск)."""
    diffs = _collect_diffs(ctx)
    if not diffs:
        log.info("Остатки уже сходятся, выравнивание не требуется")
        return []
    if dry_run:
        for d in diffs[:MAX_LINES]:
            log.info("[dry-run] «%s»: Uzum %d, МойСклад %d -> %+d",
                     d.title, d.uzum_qty, d.ms_qty, d.delta)
        log.info("[dry-run] Всего расхождений: %d", len(diffs))
        return diffs
    _correct(ctx, diffs, reason="ручное выравнивание")
    ctx.db.set_kv(KV_PREV_DIFFS, "{}")
    return diffs
