"""Поставки: перемещение при создании, списание при недостаче приёмки."""
from sync.transfers import sync_transfers
from tests.conftest import FakeResolver
from uzum.models import Invoice, InvoiceSku


def _invoice(status="CREATED", date_accepted=None):
    return Invoice(id=3705936, number="1100037059367", status=status,
                   date_accepted=date_accepted, total_to_stock=36, total_accepted=None)


def _setup(ctx, invoice, skus):
    ctx.uzum.invoice_list = [invoice]
    ctx.uzum.invoice_sku_map = {invoice.id: skus}
    ctx.resolver = FakeResolver({"PS-9908-1500МЛ": "https://ms/p9908"})


def move_posts(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/move"]


def loss_posts(ctx):
    return [p for p in ctx.ms.posts if p[0] == "/entity/loss"]


def test_move_created_for_new_invoice(ctx):
    _setup(ctx, _invoice(), [InvoiceSku(332117, "PS-9908-1500МЛ", "Чайник", 36, 0)])
    sync_transfers(ctx)

    posts = move_posts(ctx)
    assert len(posts) == 1
    payload = posts[0][1]
    assert payload["sourceStore"]["meta"]["href"].endswith("src1")
    assert payload["targetStore"]["meta"]["href"].endswith("uzum1")
    assert payload["positions"][0]["quantity"] == 36
    assert ctx.db.get_invoice(3705936)["ms_move_id"]

    sync_transfers(ctx)  # идемпотентность
    assert len(move_posts(ctx)) == 1


def test_missing_sku_blocks_move_and_notifies(ctx):
    _setup(ctx, _invoice(), [InvoiceSku(1, "НЕТУ", "X", 5, 0)])
    ctx.resolver = FakeResolver({})
    sync_transfers(ctx)
    assert move_posts(ctx) == []
    assert ctx.db.get_invoice(3705936) is None
    assert any("НЕТУ" in m for m in ctx.notifier.messages)


def test_reconcile_shortage_creates_loss(ctx):
    _setup(ctx, _invoice(), [InvoiceSku(332117, "PS-9908-1500МЛ", "Чайник", 36, 0)])
    sync_transfers(ctx)

    accepted = _invoice(status="ACCEPTED", date_accepted="2026-07-09T10:00:00")
    _setup(ctx, accepted, [InvoiceSku(332117, "PS-9908-1500МЛ", "Чайник", 36, 30)])
    sync_transfers(ctx)

    posts = loss_posts(ctx)
    assert len(posts) == 1
    payload = posts[0][1]
    assert payload["positions"][0]["quantity"] == 6
    assert "отправлено 36, принято 30" in payload["description"]
    assert "№1100037059367" in payload["description"]
    assert ctx.db.get_invoice(3705936)["reconciled"] == 1
    assert any("недостач" in m for m in ctx.notifier.messages)

    sync_transfers(ctx)  # повторная сверка не дублирует списание
    assert len(loss_posts(ctx)) == 1


def test_reconcile_without_shortage_no_loss(ctx):
    _setup(ctx, _invoice(), [InvoiceSku(332117, "PS-9908-1500МЛ", "Чайник", 36, 0)])
    sync_transfers(ctx)
    accepted = _invoice(status="ACCEPTED", date_accepted="2026-07-09T10:00:00")
    _setup(ctx, accepted, [InvoiceSku(332117, "PS-9908-1500МЛ", "Чайник", 36, 36)])
    sync_transfers(ctx)

    assert loss_posts(ctx) == []
    assert ctx.db.get_invoice(3705936)["reconciled"] == 1
