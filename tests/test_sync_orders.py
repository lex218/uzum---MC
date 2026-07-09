"""Заказы: создание, идемпотентность, ненайденный SKU, отмены."""
from sync.orders import sync_orders
from tests.conftest import FakeResolver
from tests.test_models import make_item


def _setup(ctx, items, known=None):
    ctx.uzum.order_items = items
    ctx.resolver = FakeResolver(known if known is not None else {"PS-1": "https://ms/p1"})


def order_posts(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/customerorder"]


def test_creates_customer_order(ctx):
    _setup(ctx, [make_item(id=1, orderId=100), make_item(id=2, orderId=100, skuTitle="PS-1")])
    sync_orders(ctx)

    posts = order_posts(ctx)
    assert len(posts) == 1
    payload = posts[0][1]
    assert payload["externalCode"] == "100"
    assert payload["name"] == "UZ-100"
    assert len(payload["positions"]) == 2
    pos = payload["positions"][0]
    assert pos["quantity"] == 2 and pos["reserve"] == 2
    assert pos["price"] == 45000 * 100  # сумы -> минорные единицы
    attr_values = {a["value"] for a in payload["attributes"]}
    assert "100" in attr_values  # ID заказа Uzum
    assert ctx.db.get_order(100)["status"] == "created"


def test_idempotent_second_run(ctx):
    _setup(ctx, [make_item(id=1, orderId=100)])
    sync_orders(ctx)
    sync_orders(ctx)
    assert len(order_posts(ctx)) == 1


def test_missing_sku_skips_order_and_notifies(ctx):
    _setup(ctx, [make_item(id=1, orderId=100, skuTitle="НЕИЗВЕСТНЫЙ")], known={})
    sync_orders(ctx)
    assert order_posts(ctx) == []
    assert ctx.db.get_order(100) is None  # повторим попытку следующим циклом
    assert any("НЕИЗВЕСТНЫЙ" in m for m in ctx.notifier.messages)


def test_dry_run_creates_nothing(ctx):
    _setup(ctx, [make_item(id=1, orderId=100)])
    sync_orders(ctx, dry_run=True)
    assert order_posts(ctx) == []
    assert ctx.db.get_order(100) is None


def test_cancelled_before_sync_is_skipped(ctx):
    _setup(ctx, [make_item(id=1, orderId=100, status="CANCELED")])
    sync_orders(ctx)
    assert order_posts(ctx) == []
    assert ctx.db.get_order(100)["status"] == "skipped"


def test_full_cancellation_unposts_order(ctx):
    _setup(ctx, [make_item(id=1, orderId=100)])
    sync_orders(ctx)
    ctx.uzum.order_items = [make_item(id=1, orderId=100, status="CANCELED")]
    sync_orders(ctx)

    assert ctx.db.get_order(100)["status"] == "cancelled"
    order_id = ctx.db.get_order(100)["ms_order_id"]
    put_paths = [p for p, body in ctx.ms.puts if p == f"/entity/customerorder/{order_id}"]
    assert put_paths, "ожидали PUT статуса отмены"
    body = [b for p, b in ctx.ms.puts if p == f"/entity/customerorder/{order_id}"][0]
    assert body["applicable"] is False
    # повторный прогон ничего не делает
    puts_before = len(ctx.ms.puts)
    sync_orders(ctx)
    assert len(ctx.ms.puts) == puts_before


def test_partial_cancellation_reduces_position(ctx):
    _setup(ctx, [make_item(id=1, orderId=100, amount=3)])
    sync_orders(ctx)
    rec = ctx.db.get_order(100)
    ctx.ms.rows_map[f"/entity/customerorder/{rec['ms_order_id']}/positions"] = [
        {
            "id": "pos1",
            "assortment": {"meta": {"href": "https://ms/p1", "type": "product"}},
        }
    ]
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, amount=3, cancelled=2, status="PARTIALLY_CANCELLED")
    ]
    sync_orders(ctx)

    pos_puts = [
        (p, b) for p, b in ctx.ms.puts
        if p == f"/entity/customerorder/{rec['ms_order_id']}/positions/pos1"
    ]
    assert pos_puts and pos_puts[0][1]["quantity"] == 1
    assert ctx.db.get_item(1)["qty_synced"] == 1
