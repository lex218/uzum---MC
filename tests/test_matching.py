"""Матчинг SKU: приоритет штрихкода, фолбэк на артикул, кэш в SQLite."""
from db import Db
from sync.matching import SkuResolver
from uzum.models import CatalogSku


class StubUzum:
    def __init__(self, skus):
        self._skus = skus
        self.catalog_calls = 0

    def catalog(self, shop_id):
        self.catalog_calls += 1
        return iter(self._skus)


class StubEntities:
    def __init__(self, by_barcode=None, by_article=None):
        self.by_barcode = by_barcode or {}
        self.by_article = by_article or {}
        self.calls = []

    def find_assortment(self, sku_title, barcode=None):
        self.calls.append((sku_title, barcode))
        if barcode and barcode in self.by_barcode:
            return {"href": self.by_barcode[barcode], "type": "product",
                    "name": sku_title, "matched_by": "barcode"}
        if sku_title in self.by_article:
            return {"href": self.by_article[sku_title], "type": "product",
                    "name": sku_title, "matched_by": "article"}
        return None


CATALOG = [
    CatalogSku(sku_id=1, sku_title="PS-1", product_id=10, product_title="A",
               barcode="1000001", article=None, quantity_active=5),
    CatalogSku(sku_id=2, sku_title="ЖЕЛТ", product_id=11, product_title="B",
               barcode="1000002", article=None, quantity_active=0),
]


def make_resolver(tmp_path, entities):
    db = Db(str(tmp_path / "m.sqlite3"))
    return SkuResolver(StubUzum(CATALOG), entities, db, 387), db


def test_barcode_has_priority(tmp_path):
    ents = StubEntities(by_barcode={"1000001": "https://ms/p1"},
                        by_article={"PS-1": "https://ms/wrong"})
    resolver, _ = make_resolver(tmp_path, ents)
    found = resolver.resolve("PS-1", product_id=10)
    assert found["href"] == "https://ms/p1"
    assert found["matched_by"] == "barcode"
    # штрихкод взят из каталога по (productId, skuTitle)
    assert ents.calls == [("PS-1", "1000001")]


def test_fallback_to_article(tmp_path):
    ents = StubEntities(by_article={"PS-1": "https://ms/p1"})
    resolver, _ = make_resolver(tmp_path, ents)
    assert resolver.resolve("PS-1", product_id=10)["matched_by"] == "article"


def test_unknown_sku_returns_none(tmp_path):
    resolver, _ = make_resolver(tmp_path, StubEntities())
    assert resolver.resolve("НЕТ-ТАКОГО", product_id=99) is None


def test_cache_avoids_repeat_lookup(tmp_path):
    ents = StubEntities(by_barcode={"1000001": "https://ms/p1"})
    resolver, db = make_resolver(tmp_path, ents)
    resolver.resolve("PS-1", sku_id=1)
    resolver.resolve("PS-1", sku_id=1)
    assert len(ents.calls) == 1  # второй раз — из кэша SQLite
    assert db.get_sku("sku:1")["ms_href"] == "https://ms/p1"
