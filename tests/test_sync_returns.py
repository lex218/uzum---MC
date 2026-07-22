"""Возвраты после выкупа: дельта amountReturns -> возврат покупателя."""
from sync.orders import sync_orders
from sync.returns import sync_returns
from sync.shipments import sync_shipments
from tests.conftest import FakeResolver
from tests.test_models import make_item


def _shipped_order(ctx):
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1"})
    ctx.uzum.order_items = [make_item(id=1, orderId=100, amount=2)]
    sync_orders(ctx)
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, amount=2, status="TO_WITHDRAW", dateIssued=1783600000000)
    ]
    sync_orders(ctx)
    sync_shipments(ctx)


def return_posts(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/salesreturn"]


def test_return_created_for_delta(ctx):
    _shipped_order(ctx)
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, amount=2, status="TO_WITHDRAW",
                  dateIssued=1783600000000, amountReturns=1, returnCause="Брак")
    ]
    sync_orders(ctx)
    sync_returns(ctx)

    posts = return_posts(ctx)
    assert len(posts) == 1
    payload = posts[0][1]
    assert payload["positions"][0]["quantity"] == 1
    assert payload["demand"]["meta"]["type"] == "demand"
    assert "Брак" in payload["description"]
    assert ctx.db.get_order(100)["status"] == "returned"
    assert ctx.db.get_item(1)["returns_synced"] == 1


def test_return_idempotent_and_incremental(ctx):
    _shipped_order(ctx)
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, amount=2, status="TO_WITHDRAW",
                  dateIssued=1783600000000, amountReturns=1)
    ]
    sync_orders(ctx)
    sync_returns(ctx)
    sync_returns(ctx)  # без изменений — дубля нет
    assert len(return_posts(ctx)) == 1

    # вернулась вторая штука — ещё один возврат на дельту
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, amount=2, status="TO_WITHDRAW",
                  dateIssued=1783600000000, amountReturns=2)
    ]
    sync_orders(ctx)
    sync_returns(ctx)
    posts = return_posts(ctx)
    assert len(posts) == 2
    assert posts[1][1]["positions"][0]["quantity"] == 1
    assert ctx.db.get_item(1)["returns_synced"] == 2


def test_no_return_without_returns(ctx):
    _shipped_order(ctx)
    sync_returns(ctx)
    assert return_posts(ctx) == []


def test_returned_after_buyout_ships_and_returns(ctx):
    """CANCELED + amountReturns: заказ не отменяется, а отгружается и возвращается."""
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1"})
    ctx.uzum.order_items = [make_item(id=1, orderId=100, amount=1)]
    sync_orders(ctx)
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, amount=1, status="CANCELED",
                  dateIssued=1783600000000, amountReturns=1, returnCause="Возврат")
    ]
    sync_orders(ctx)
    assert ctx.db.get_order(100)["status"] == "created"  # не отменён
    sync_shipments(ctx)
    sync_returns(ctx)

    demand = [p for p in ctx.ms.posts if p[0] == "/entity/demand"]
    assert len(demand) == 1
    posts = return_posts(ctx)
    assert len(posts) == 1
    assert posts[0][1]["positions"][0]["quantity"] == 1
    assert ctx.db.get_order(100)["status"] == "returned"


def test_unshipped_position_closes_without_document(ctx):
    """Позиция, отменённая до отгрузки, но с amountReturns у Uzum, — без возврата."""
    _shipped_order(ctx)
    order_id = "100"
    # искусственно добавляем неотгруженную позицию с возвратом
    ctx.db.upsert_item(99, order_id, "PS-X", None, None, 100, 0, "CANCELED", None,
                       amount_returns=1)
    sync_returns(ctx)
    assert return_posts(ctx) == []
    assert ctx.db.get_item(99)["returns_synced"] == 1  # дельта закрыта
