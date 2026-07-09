"""Отгрузки: создаются после выкупа всех активных позиций."""
from sync.orders import sync_orders
from sync.shipments import sync_shipments
from tests.conftest import FakeResolver
from tests.test_models import make_item


def _create_order(ctx, items):
    ctx.uzum.order_items = items
    ctx.resolver = FakeResolver({"PS-1": "https://ms/p1", "PS-2": "https://ms/p2"})
    sync_orders(ctx)


def demand_posts(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/demand"]


def test_no_demand_until_issued(ctx):
    _create_order(ctx, [make_item(id=1, orderId=100)])
    sync_shipments(ctx)
    assert demand_posts(ctx) == []


def test_demand_created_when_all_issued(ctx):
    _create_order(ctx, [
        make_item(id=1, orderId=100),
        make_item(id=2, orderId=100, skuTitle="PS-2"),
    ])
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, status="TO_WITHDRAW", dateIssued=1783600000000),
        make_item(id=2, orderId=100, skuTitle="PS-2", status="TO_WITHDRAW",
                  dateIssued=1783600000000),
    ]
    sync_orders(ctx)
    sync_shipments(ctx)

    posts = demand_posts(ctx)
    assert len(posts) == 1
    payload = posts[0][1]
    assert payload["customerOrder"]["meta"]["type"] == "customerorder"
    assert len(payload["positions"]) == 2
    rec = ctx.db.get_order(100)
    assert rec["status"] == "shipped" and rec["ms_demand_id"]

    # идемпотентность
    sync_shipments(ctx)
    assert len(demand_posts(ctx)) == 1


def test_partial_issue_waits(ctx):
    _create_order(ctx, [
        make_item(id=1, orderId=100),
        make_item(id=2, orderId=100, skuTitle="PS-2"),
    ])
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, status="TO_WITHDRAW", dateIssued=1783600000000),
        make_item(id=2, orderId=100, skuTitle="PS-2"),  # ещё не выкуплен
    ]
    sync_orders(ctx)
    sync_shipments(ctx)
    assert demand_posts(ctx) == []


def test_cancelled_position_excluded_from_demand(ctx):
    _create_order(ctx, [
        make_item(id=1, orderId=100),
        make_item(id=2, orderId=100, skuTitle="PS-2"),
    ])
    rec = ctx.db.get_order(100)
    ctx.ms.rows_map[f"/entity/customerorder/{rec['ms_order_id']}/positions"] = [
        {"id": "pos2", "assortment": {"meta": {"href": "https://ms/p2", "type": "product"}}},
    ]
    ctx.uzum.order_items = [
        make_item(id=1, orderId=100, status="TO_WITHDRAW", dateIssued=1783600000000),
        make_item(id=2, orderId=100, skuTitle="PS-2", status="CANCELED"),
    ]
    sync_orders(ctx)
    sync_shipments(ctx)

    posts = demand_posts(ctx)
    assert len(posts) == 1
    assert len(posts[0][1]["positions"]) == 1  # только выкупленная позиция
