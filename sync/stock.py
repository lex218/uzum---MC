"""Сверка остатков склада «Uzum» в МойСклад с остатками FBO из Uzum API.

Сравнивается quantityActive каждого SKU каталога Uzum с физическим
остатком (stockType=stock) на складе «Uzum». Расхождения не корректируются
автоматически — отправляется сводное уведомление в Telegram.
"""
from __future__ import annotations

import logging

from sync.context import SyncContext

log = logging.getLogger(__name__)

MAX_LINES = 30


def check_stock(ctx: SyncContext) -> list[str]:
    store_href = ctx.entities.store_uzum()["meta"]["href"]
    store_id = store_href.rstrip("/").rsplit("/", 1)[-1]

    rows = ctx.ms.get("/report/stock/bystore/current", {"stockType": "stock"}) or []
    ms_stock = {r["assortmentId"]: r["quantity"] for r in rows if r.get("storeId") == store_id}

    diffs: list[str] = []
    for sku in ctx.resolver.catalog_skus():
        found = ctx.resolver.resolve(sku.sku_title, product_id=sku.product_id, sku_id=sku.sku_id)
        if found is None:
            continue  # несматченные SKU уже приводят к уведомлениям в других модулях
        assortment_id = found["href"].rstrip("/").rsplit("/", 1)[-1]
        ms_qty = int(ms_stock.get(assortment_id, 0))
        if ms_qty != sku.quantity_active:
            diffs.append(
                f"«{sku.sku_title}»: Uzum {sku.quantity_active}, МойСклад {ms_qty}"
            )

    if diffs:
        shown = diffs[:MAX_LINES]
        more = f"\n… и ещё {len(diffs) - MAX_LINES}" if len(diffs) > MAX_LINES else ""
        ctx.notifier.send(
            f"📊 Uzum-sync: расхождения остатков склада «Uzum» ({len(diffs)} SKU):\n"
            + "\n".join(shown) + more
        )
    else:
        log.info("Остатки склада «Uzum» сходятся с Uzum API")
    return diffs
