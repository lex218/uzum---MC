"""Клиент Uzum Seller API (api-seller.uzum.uz, схема FBO).

Авторизация: заголовок `Authorization: <токен>` БЕЗ префикса Bearer.
Фильтры dateFrom/dateTo принимают unix-время в СЕКУНДАХ,
в ответах даты — в миллисекундах (проверено живыми запросами).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from config import UZUM_BASE_URL
from uzum.models import CatalogSku, Invoice, InvoiceSku, OrderItem

log = logging.getLogger(__name__)

PAGE_SIZE = 50  # максимум для finance/orders и invoice
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5


class UzumApiError(Exception):
    pass


class UzumClient:
    def __init__(
        self,
        token: str,
        base_url: str = UZUM_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={"Authorization": token},
            timeout=30.0,
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise UzumApiError(f"Uzum {path}: сетевая ошибка: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                delay = float(resp.headers.get("Retry-After") or 2**attempt)
                log.warning("Uzum %s: HTTP %s, повтор через %.1fс", path, resp.status_code, delay)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise UzumApiError(f"Uzum {path}: HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise UzumApiError(f"Uzum {path}: исчерпаны попытки")

    # --- магазины ---
    def shops(self) -> list[dict[str, Any]]:
        return self._get("/v1/shops")

    def default_shop_id(self) -> int:
        shops = self.shops()
        if not shops:
            raise UzumApiError("В аккаунте Uzum нет магазинов")
        return shops[0]["id"]

    # --- заказы (FBO) ---
    def finance_orders(
        self,
        shop_id: int,
        date_from_s: int | None = None,
        date_to_s: int | None = None,
    ) -> Iterator[OrderItem]:
        """Все позиции заказов за период (строка = SKU заказа)."""
        page = 0
        while True:
            params: dict[str, Any] = {"shopIds": shop_id, "page": page, "size": PAGE_SIZE}
            if date_from_s is not None:
                params["dateFrom"] = int(date_from_s)
            if date_to_s is not None:
                params["dateTo"] = int(date_to_s)
            data = self._get("/v1/finance/orders", params)
            items = data.get("orderItems") or []
            for it in items:
                yield OrderItem.from_api(it)
            if len(items) < PAGE_SIZE:
                return
            page += 1

    # --- поставки ---
    def invoices(self, shop_id: int) -> Iterator[Invoice]:
        """Накладные от новых к старым (ленивая пагинация)."""
        page = 0
        while True:
            data = self._get(
                f"/v1/shop/{shop_id}/invoice", {"page": page, "size": PAGE_SIZE}
            )
            for d in data or []:
                yield Invoice.from_api(d)
            if not data or len(data) < PAGE_SIZE:
                return
            page += 1

    def invoice_skus(self, shop_id: int, invoice_id: int) -> list[InvoiceSku]:
        data = self._get(f"/v1/shop/{shop_id}/invoice/products", {"invoiceId": invoice_id})
        return InvoiceSku.list_from_api(data or [])

    # --- каталог и остатки FBO ---
    def catalog(self, shop_id: int) -> Iterator[CatalogSku]:
        page = 0
        while True:
            data = self._get(f"/v1/product/shop/{shop_id}", {"page": page, "size": PAGE_SIZE})
            products = data.get("productList") or []
            for p in products:
                yield from CatalogSku.list_from_product(p)
            if len(products) < PAGE_SIZE:
                return
            page += 1
