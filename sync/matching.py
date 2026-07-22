"""Матчинг SKU Uzum -> ассортимент МойСклад.

Приоритет — штрихкод Uzum, занесённый в карточку МойСклад; запасной
вариант — артикул товара / код модификации, равный skuTitle.
В заказах и накладных штрихкода нет, поэтому он резолвится через каталог
/v1/product/shop/{shopId} по skuId или (productId, skuTitle).
Найденные соответствия кэшируются в SQLite.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from db import Db
from uzum.models import CatalogSku

log = logging.getLogger(__name__)

# в длительном run-процессе каталог (остатки quantityActive!) должен
# перечитываться, иначе сверка остатков сравнивает живой МС с застывшим Uzum
CATALOG_TTL_S = 600


class SkuResolver:
    def __init__(self, uzum: Any, entities: Any, db: Db, shop_id: int) -> None:
        self._uzum = uzum
        self._entities = entities
        self._db = db
        self._shop_id = shop_id
        self._by_sku_id: dict[int, CatalogSku] | None = None
        self._by_product_title: dict[tuple[int, str], CatalogSku] = {}
        self._loaded_at: float = 0.0

    def _load_catalog(self, max_age_s: float | None = None) -> None:
        if self._by_sku_id is not None and (
            max_age_s is None or time.monotonic() - self._loaded_at <= max_age_s
        ):
            return
        self._by_sku_id = {}
        self._by_product_title = {}
        for sku in self._uzum.catalog(self._shop_id):
            self._by_sku_id[sku.sku_id] = sku
            self._by_product_title[(sku.product_id, sku.sku_title)] = sku
        self._loaded_at = time.monotonic()
        log.info("Каталог Uzum загружен: %d SKU", len(self._by_sku_id))

    def catalog_skus(self) -> list[CatalogSku]:
        self._load_catalog(max_age_s=CATALOG_TTL_S)
        assert self._by_sku_id is not None
        return list(self._by_sku_id.values())

    def _catalog_lookup(
        self, sku_id: int | None, product_id: int | None, sku_title: str
    ) -> CatalogSku | None:
        self._load_catalog()
        assert self._by_sku_id is not None
        if sku_id is not None and sku_id in self._by_sku_id:
            return self._by_sku_id[sku_id]
        if product_id is not None:
            return self._by_product_title.get((product_id, sku_title))
        return None

    def resolve(
        self,
        sku_title: str,
        product_id: int | None = None,
        sku_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Вернуть {href, type, name, matched_by} позиции МойСклад либо None."""
        catalog_sku = self._catalog_lookup(sku_id, product_id, sku_title)
        key = f"sku:{catalog_sku.sku_id}" if catalog_sku else f"title:{product_id}:{sku_title}"

        cached = self._db.get_sku(key)
        if cached:
            return {
                "href": cached["ms_href"],
                "type": cached["ms_type"],
                "name": cached["name"],
                "matched_by": cached["matched_by"],
            }

        barcode = catalog_sku.barcode if catalog_sku else None
        found = self._entities.find_assortment(sku_title, barcode=barcode)
        if found is None:
            log.warning(
                "SKU не найден в МойСклад: «%s» (productId=%s, barcode=%s)",
                sku_title, product_id, barcode,
            )
            return None
        self._db.put_sku(key, found["href"], found["type"], found["matched_by"], found["name"])
        return found
