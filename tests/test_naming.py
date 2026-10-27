"""Режим совместимости с серверной синхронизацией (DOC_STYLE=uzum-supply)."""
from dataclasses import replace

from config import Config
from sync.naming import Naming
from sync.orders import sync_orders
from sync.transfers import sync_transfers
from tests.conftest import FakeResolver
from tests.test_models import make_item
from tests.test_sync_transfers import _invoice, _setup as setup_invoice
from uzum.models import InvoiceSku


def compat(ctx):
    ctx.cfg = replace(ctx.cfg, doc_style="uzum-supply", shop_label="ComFore shop")
    return ctx


def test_naming_native_vs_compat():
    native = Naming(Config(uzum_token="t", ms_token="t"))
    c = Naming(Config(uzum_token="t", ms_token="t", doc_style="uzum-supply"))
    assert native.order_ext(5) == "5" and c.order_ext(5) == "uzum-o-5"
    assert c.demand_ext(5) == "uzum-d-5" and c.move_ext(7) == "uzum-inv-7"
    assert c.loss_ext(7) == "uzum-inv-loss-7"
    assert c.return_ext(5, 0, 1) == "uzum-r-5" and c.return_ext(5, 1, 2) == "uzum-r-5-2"
    assert c.order_name(5) is None and native.order_name(5) == "UZ-5"


def test_compat_order_payload(ctx):
    compat(ctx)
    ctx.uzum.order_items = [make_item(id=1, orderId=777)]
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1"})
    sync_orders(ctx)
    path, payload = [p for p in ctx.ms.posts if p[0] == "/entity/customerorder"][0]
    assert payload["externalCode"] == "uzum-o-777"
    assert "name" not in payload  # номер ставит МойСклад
    assert payload["description"] == "Заказ Uzum 777, магазин ComFore shop"


def test_compat_existing_doc_from_server_not_duplicated(ctx):
    """Если заказ уже создан серверной программой — второй не создаём."""
    compat(ctx)
    ctx.uzum.order_items = [make_item(id=1, orderId=777)]
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1"})
    ctx.ms.find_one_map[("/entity/customerorder", "externalCode=uzum-o-777")] = {
        "id": "srv1", "meta": {"href": "https://ms/co/srv1", "type": "customerorder"}}
    sync_orders(ctx)
    assert [p for p in ctx.ms.posts if p[0] == "/entity/customerorder"] == []
    assert ctx.db.get_order(777)["ms_order_id"] == "srv1"


def test_compat_move_payload(ctx):
    compat(ctx)
    setup_invoice(ctx, _invoice(), [InvoiceSku(332117, "PS-9908-1500МЛ", "Чайник", 36, 0)])
    sync_transfers(ctx)
    path, payload = [p for p in ctx.ms.posts if p[0] == "/entity/move"][0]
    assert payload["externalCode"] == "uzum-inv-3705936"
    assert "name" not in payload
    assert "магазин ComFore shop" in payload["description"]


def test_dry_run_order_touches_nothing(ctx):
    """dry-run не должен даже собирать документ (это может создавать служебные записи)."""
    ctx.uzum.order_items = [make_item(id=1, orderId=777)]
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1"})
    calls = []
    ctx.entities.agent = lambda: calls.append("agent")
    sync_orders(ctx, dry_run=True)
    assert ctx.ms.posts == [] and calls == []


def test_dry_run_reports_existing_docs(ctx, caplog):
    """На новой базе dry-run не должен обещать создать то, что уже есть в МС."""
    import logging
    compat(ctx)
    ctx.uzum.order_items = [make_item(id=1, orderId=777)]
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1"})
    ctx.ms.find_one_map[("/entity/customerorder", "externalCode=uzum-o-777")] = {"name": "02027"}
    with caplog.at_level(logging.INFO):
        sync_orders(ctx, dry_run=True)
    assert "уже есть в МС (02027)" in caplog.text
    assert ctx.ms.posts == []
