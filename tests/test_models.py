from uzum.models import CatalogSku, Invoice, InvoiceSku, OrderItem


def make_item(**kw):
    base = dict(
        id=1, orderId=100, status="PROCESSING", date=1783596038123,
        dateIssued=None, skuTitle="PS-1", productId=10, productTitle="Товар",
        amount=2, amountReturns=0, cancelled=None, sellPrice=45000,
        commission=11250, logisticDeliveryFee=5750, returnCause=None,
    )
    base.update(kw)
    return OrderItem.from_api(base)


def test_order_item_effective_qty():
    assert make_item().effective_qty == 2
    assert make_item(cancelled=1).effective_qty == 1
    assert make_item(status="CANCELED").effective_qty == 0


def test_order_item_issued():
    assert not make_item().issued
    assert make_item(status="TO_WITHDRAW").issued
    assert make_item(dateIssued=1783596038123).issued


def test_invoice_acceptance():
    inv = Invoice.from_api(
        {"id": 5, "invoiceNumber": 110, "invoiceStatus": {"value": "CREATED"}}
    )
    assert inv.status == "CREATED" and not inv.acceptance_finished
    inv2 = Invoice.from_api(
        {"id": 5, "invoiceNumber": 110, "invoiceStatus": {"value": "ACCEPTED"}}
    )
    assert inv2.acceptance_finished
    inv3 = Invoice.from_api(
        {"id": 5, "invoiceNumber": 110, "status": "X", "dateAccepted": "2026-07-08T10:00:00"}
    )
    assert inv3.acceptance_finished


def test_invoice_skus_flatten():
    skus = InvoiceSku.list_from_api(
        [
            {
                "productTitle": "Чайник",
                "skuForInvoiceDtoList": [
                    {"id": 1, "skuTitle": "PS-1", "quantityToStock": 36, "quantityAccepted": 30},
                ],
            }
        ]
    )
    assert skus == [InvoiceSku(1, "PS-1", "Чайник", 36, 30)]


def test_catalog_sku_barcode_str():
    skus = CatalogSku.list_from_product(
        {
            "productId": 30990,
            "title": "Плёнка",
            "skuList": [
                {"skuId": 276221, "skuTitle": "CЕРМЕЛ-60Х2", "barcode": 1000002762219,
                 "article": None, "quantityActive": 7}
            ],
        }
    )
    assert skus[0].barcode == "1000002762219"
    assert skus[0].quantity_active == 7
