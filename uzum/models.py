"""Модели данных Uzum Seller API (только используемые поля)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# Статусы позиций заказа в /v1/finance/orders
STATUS_PROCESSING = "PROCESSING"
STATUS_TO_WITHDRAW = "TO_WITHDRAW"  # выкуплен
STATUS_CANCELED = "CANCELED"
STATUS_PARTIALLY_CANCELLED = "PARTIALLY_CANCELLED"


@dataclass(frozen=True)
class OrderItem:
    """Позиция заказа из GET /v1/finance/orders (строка = один SKU заказа)."""

    id: int
    order_id: int
    status: str
    date_ms: int
    date_issued_ms: int | None
    sku_title: str
    product_id: int | None
    product_title: str | None
    amount: int
    amount_returns: int
    cancelled: int
    sell_price: int  # сумы за единицу
    commission: int
    logistic_fee: int
    return_cause: str | None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "OrderItem":
        return cls(
            id=d["id"],
            order_id=d["orderId"],
            status=d.get("status") or "",
            date_ms=d.get("date") or 0,
            date_issued_ms=d.get("dateIssued"),
            sku_title=d.get("skuTitle") or "",
            product_id=d.get("productId"),
            product_title=d.get("productTitle"),
            amount=d.get("amount") or 0,
            amount_returns=d.get("amountReturns") or 0,
            cancelled=d.get("cancelled") or 0,
            sell_price=d.get("sellPrice") or 0,
            commission=d.get("commission") or 0,
            logistic_fee=d.get("logisticDeliveryFee") or 0,
            return_cause=d.get("returnCause"),
        )

    @property
    def effective_qty(self) -> int:
        """Количество к продаже с учётом отмен.

        Возврат после выкупа Uzum помечает как CANCELED с amountReturns > 0 —
        это НЕ отмена: товар был продан и вернулся, позиция остаётся в заказе
        (отгрузка + возврат покупателя), иначе позицию считаем отменённой.
        """
        if self.status == STATUS_CANCELED and self.amount_returns == 0:
            return 0
        return max(self.amount - self.cancelled, 0)

    @property
    def issued(self) -> bool:
        """Выкуплен (выдан покупателю)."""
        return (
            self.status == STATUS_TO_WITHDRAW
            or self.date_issued_ms is not None
            or self.amount_returns > 0
        )


@dataclass(frozen=True)
class Invoice:
    """Поставка (накладная) на склад FBO из GET /v1/shop/{shopId}/invoice."""

    id: int
    number: str
    status: str  # invoiceStatus.value: CREATED / ... / ACCEPTED
    date_created: date | None  # dateCreated приходит строкой "дд.мм.гггг"
    date_accepted: str | None
    total_to_stock: int | None
    total_accepted: int | None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Invoice":
        created = None
        raw = d.get("dateCreated")
        if raw:
            try:
                created = datetime.strptime(raw, "%d.%m.%Y").date()
            except ValueError:
                pass
        return cls(
            id=d["id"],
            number=str(d.get("invoiceNumber") or d["id"]),
            status=((d.get("invoiceStatus") or {}).get("value") or d.get("status") or ""),
            date_created=created,
            date_accepted=d.get("dateAccepted"),
            total_to_stock=d.get("totalToStock"),
            total_accepted=d.get("totalAccepted"),
        )

    @property
    def acceptance_finished(self) -> bool:
        return bool(self.date_accepted) or self.status == "ACCEPTED"


@dataclass(frozen=True)
class InvoiceSku:
    """SKU в составе поставки (GET /v1/shop/{shopId}/invoice/products)."""

    sku_id: int
    sku_title: str
    product_title: str | None
    to_stock: int  # отправлено
    accepted: int  # принято складом

    @classmethod
    def list_from_api(cls, products: list[dict[str, Any]]) -> list["InvoiceSku"]:
        out: list[InvoiceSku] = []
        for p in products:
            for s in p.get("skuForInvoiceDtoList") or []:
                out.append(
                    cls(
                        sku_id=s["id"],
                        sku_title=s.get("skuTitle") or "",
                        product_title=p.get("productTitle"),
                        to_stock=s.get("quantityToStock") or 0,
                        accepted=s.get("quantityAccepted") or 0,
                    )
                )
        return out


@dataclass(frozen=True)
class CatalogSku:
    """SKU из каталога товаров GET /v1/product/shop/{shopId} (штрихкод, остаток FBO)."""

    sku_id: int
    sku_title: str
    product_id: int
    product_title: str | None
    barcode: str | None
    article: str | None
    quantity_active: int
    purchase_price: int  # закупочная, сумы

    @classmethod
    def list_from_product(cls, p: dict[str, Any]) -> list["CatalogSku"]:
        out: list[CatalogSku] = []
        for s in p.get("skuList") or []:
            barcode = s.get("barcode")
            out.append(
                cls(
                    sku_id=s["skuId"],
                    sku_title=s.get("skuTitle") or "",
                    product_id=p["productId"],
                    product_title=p.get("title"),
                    barcode=str(barcode) if barcode else None,
                    article=s.get("article") or s.get("sellerItemCode"),
                    quantity_active=s.get("quantityActive") or 0,
                    purchase_price=s.get("purchasePrice") or 0,
                )
            )
        return out
