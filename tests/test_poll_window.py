"""Окно опроса заказов: перекрытие не опускается ниже точки первого запуска."""
from sync.orders import KV_INITIAL_START, KV_LAST_POLL, _poll_window

NOW = 1_800_000_000
DAY = 86400


def test_first_run_uses_initial_days(ctx):
    assert _poll_window(ctx, NOW) == NOW - ctx.cfg.orders_initial_days * DAY


def test_overlap_does_not_go_below_initial_start(ctx):
    start = NOW - 1 * DAY
    ctx.db.set_kv(KV_INITIAL_START, str(start))
    ctx.db.set_kv(KV_LAST_POLL, str(NOW))
    # перекрытие 48ч увело бы на 2 дня назад, но пол — точка первого запуска
    assert _poll_window(ctx, NOW + 60) == start


def test_open_order_extends_window(ctx):
    start = NOW - 1 * DAY
    ctx.db.set_kv(KV_INITIAL_START, str(start))
    ctx.db.set_kv(KV_LAST_POLL, str(NOW))
    old_open_ms = (NOW - 10 * DAY) * 1000
    ctx.db.upsert_order(1, "created", ms_order_id="x", first_seen_ms=old_open_ms)
    assert _poll_window(ctx, NOW + 60) == old_open_ms // 1000 - 3600
