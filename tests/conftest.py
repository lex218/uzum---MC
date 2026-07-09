"""Общие фейки для тестов sync-модулей (без сети)."""
from __future__ import annotations

from typing import Any

import pytest

from config import Config
from db import Db
from sync.context import SyncContext


def _meta(href: str, type_: str) -> dict[str, Any]:
    return {"meta": {"href": href, "type": type_, "mediaType": "application/json"}}


class FakeMS:
    """Запоминает вызовы; ответы настраиваются словарями."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, Any]] = []
        self.puts: list[tuple[str, Any]] = []
        self.deletes: list[str] = []
        self.find_one_map: dict[tuple[str, str], Any] = {}
        self.rows_map: dict[str, list[dict]] = {}
        self.get_map: dict[str, Any] = {}
        self._seq = 0

    def post(self, path: str, json: Any) -> dict[str, Any]:
        self.posts.append((path, json))
        self._seq += 1
        entity = path.rstrip("/").rsplit("/", 1)[-1]
        return {
            "id": f"{entity}-{self._seq}",
            "name": json.get("name", "") if isinstance(json, dict) else "",
            "meta": {
                "href": f"https://ms.test{path}/{entity}-{self._seq}",
                "type": entity,
                "mediaType": "application/json",
            },
        }

    def put(self, path: str, json: Any) -> dict[str, Any]:
        self.puts.append((path, json))
        return {}

    def delete(self, path: str) -> None:
        self.deletes.append(path)

    def find_one(self, path: str, filter_: str) -> Any:
        return self.find_one_map.get((path, filter_))

    def rows(self, path: str, filter_: str | None = None, params: Any = None) -> list[dict]:
        return self.rows_map.get(path, [])

    def get(self, path: str, params: Any = None) -> Any:
        return self.get_map.get(path)


class FakeEntities:
    def organization(self) -> dict[str, Any]:
        return _meta("https://ms.test/entity/organization/org1", "organization") | {"name": "Org"}

    def agent(self) -> dict[str, Any]:
        return _meta("https://ms.test/entity/counterparty/agent1", "counterparty") | {"name": "Uzum"}

    def store_source(self) -> dict[str, Any]:
        return _meta("https://ms.test/entity/store/src1", "store") | {"name": "Фулфилмент Евгений"}

    def store_uzum(self) -> dict[str, Any]:
        return _meta("https://ms.test/entity/store/uzum1", "store") | {"name": "Uzum"}

    def attr_values(self, entity_type: str, values: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"meta": {"href": f"https://ms.test/attr/{entity_type}/{k}"}, "value": v}
            for k, v in values.items()
            if v is not None
        ]

    def order_state_ref(self, name: str) -> dict[str, Any]:
        return {"meta": {"href": f"https://ms.test/state/{name}", "type": "state"}}

    def find_assortment(self, sku_title: str, barcode: str | None = None) -> None:
        return None


class FakeResolver:
    """Резолвер по заранее заданной таблице sku_title -> href."""

    def __init__(self, known: dict[str, str] | None = None) -> None:
        self.known = known or {}
        self.calls: list[str] = []

    def resolve(self, sku_title: str, product_id: int | None = None, sku_id: int | None = None):
        self.calls.append(sku_title)
        href = self.known.get(sku_title)
        if href is None:
            return None
        return {"href": href, "type": "product", "name": sku_title, "matched_by": "test"}

    def catalog_skus(self):
        return []


class FakeUzum:
    def __init__(self) -> None:
        self.order_items: list = []
        self.invoice_list: list = []
        self.invoice_sku_map: dict[int, list] = {}

    def finance_orders(self, shop_id, date_from_s=None, date_to_s=None):
        yield from self.order_items

    def invoices(self, shop_id):
        return self.invoice_list

    def invoice_skus(self, shop_id, invoice_id):
        return self.invoice_sku_map.get(invoice_id, [])

    def catalog(self, shop_id):
        return iter([])


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


@pytest.fixture()
def ctx(tmp_path) -> SyncContext:
    cfg = Config(uzum_token="t", ms_token="t", orders_initial_days=1)
    return SyncContext(
        cfg=cfg,
        db=Db(str(tmp_path / "test.sqlite3")),
        uzum=FakeUzum(),
        ms=FakeMS(),
        entities=FakeEntities(),
        resolver=FakeResolver(),
        notifier=FakeNotifier(),
        shop_id=387,
    )
