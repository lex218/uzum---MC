"""Сверка остатков: автокоррекция стабильных расхождений, align-stock."""
from sync.stock import align_stock, check_stock
from uzum.models import CatalogSku


class StockResolver:
    def __init__(self, skus, href_map):
        self._skus = skus
        self._map = href_map

    def catalog_skus(self):
        return self._skus

    def resolve(self, sku_title, product_id=None, sku_id=None):
        href = self._map.get(sku_title)
        if href is None:
            return None
        return {"href": href, "type": "product", "name": sku_title, "matched_by": "test"}


def _sku(sku_id, title, qty, purchase=10000):
    return CatalogSku(sku_id=sku_id, sku_title=title, product_id=1, product_title="T",
                      barcode=str(sku_id), article=None, quantity_active=qty,
                      purchase_price=purchase)


def _setup(ctx, uzum_qty: dict[str, int], ms_qty: dict[str, int]):
    """uzum_qty/ms_qty: sku_title -> количество; href = https://ms/{title}."""
    skus = [_sku(i, t, q) for i, (t, q) in enumerate(uzum_qty.items(), start=1)]
    ctx.resolver = StockResolver(skus, {t: f"https://ms/{t}" for t in uzum_qty})
    # storeId склада «Uzum» из FakeEntities — uzum1
    ctx.ms.get_map["/report/stock/bystore/current"] = [
        {"assortmentId": t, "storeId": "uzum1", "freeStock": q} for t, q in ms_qty.items()
    ] + [{"assortmentId": "чужой", "storeId": "other", "freeStock": 99}]


def enters(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/enter"]


def losses(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/loss"]


def test_no_diff_no_docs(ctx):
    _setup(ctx, {"PS-1": 5}, {"PS-1": 5})
    assert check_stock(ctx) == []
    assert enters(ctx) == [] and losses(ctx) == []


def test_fresh_diff_waits_for_confirmation(ctx):
    _setup(ctx, {"PS-1": 5}, {"PS-1": 3})
    diffs = check_stock(ctx)
    assert len(diffs) == 1 and diffs[0].delta == 2
    assert enters(ctx) == [] and losses(ctx) == []  # первая сверка — только снапшот


def test_stable_diff_autocorrected(ctx):
    _setup(ctx, {"PS-1": 5, "PS-2": 1}, {"PS-1": 3, "PS-2": 4})
    check_stock(ctx)
    check_stock(ctx)  # то же расхождение второй раз — корректируем

    ent = enters(ctx)
    assert len(ent) == 1
    pos = ent[0][1]["positions"][0]
    assert pos["quantity"] == 2  # нашлись 2 шт PS-1
    assert pos["price"] == 10000 * 100  # себестоимость из закупочной Uzum
    assert "Uzum" in ent[0][1]["description"]

    los = losses(ctx)
    assert len(los) == 1
    assert los[0][1]["positions"][0]["quantity"] == 3  # потерялись 3 шт PS-2
    assert any("скорректированы" in m for m in ctx.notifier.messages)


def test_changed_diff_not_corrected(ctx):
    _setup(ctx, {"PS-1": 5}, {"PS-1": 3})
    check_stock(ctx)
    _setup(ctx, {"PS-1": 6}, {"PS-1": 3})  # дельта изменилась (2 -> 3)
    check_stock(ctx)
    assert enters(ctx) == []


def test_autocorrect_disabled_only_notifies(ctx):
    ctx.cfg = ctx.cfg.__class__(uzum_token="t", ms_token="t", stock_autocorrect=False)
    _setup(ctx, {"PS-1": 5}, {"PS-1": 3})
    check_stock(ctx)
    check_stock(ctx)
    assert enters(ctx) == []
    assert any("автокоррекция выключена" in m for m in ctx.notifier.messages)


def test_align_stock_corrects_immediately(ctx):
    _setup(ctx, {"PS-1": 10}, {})  # в МС пусто — первичная загрузка
    align_stock(ctx)
    ent = enters(ctx)
    assert len(ent) == 1 and ent[0][1]["positions"][0]["quantity"] == 10


def test_in_transit_invoice_excluded_from_diff(ctx):
    # накладная отправлена (5 шт PS-1, sku_id=1), приёмка не завершена:
    # в МС остаток уже есть, в Uzum ещё нет — расхождения быть не должно
    _setup(ctx, {"PS-1": 0}, {"PS-1": 5})
    ctx.db.add_invoice(1, "INV1", "m1", "https://ms/move/m1", {"1": 5})
    assert check_stock(ctx) == []


def test_align_stock_dry_run(ctx):
    _setup(ctx, {"PS-1": 10}, {})
    diffs = align_stock(ctx, dry_run=True)
    assert len(diffs) == 1
    assert enters(ctx) == []
