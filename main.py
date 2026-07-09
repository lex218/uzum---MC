"""CLI и цикл опроса сервиса синхронизации Uzum Market -> МойСклад.

Команды:
    python main.py init            -- проверить доступы, создать доп. поля/статусы/контрагента
    python main.py sync-orders     -- заказы Uzum -> заказы покупателя
    python main.py sync-shipments  -- выкупы -> отгрузки
    python main.py sync-returns    -- возвраты -> возвраты покупателя
    python main.py sync-transfers  -- поставки -> перемещения (+списания)
    python main.py check-stock     -- сверка остатков склада «Uzum»
    python main.py sync-all        -- один полный цикл
    python main.py run             -- бесконечный цикл по интервалам из конфига
Флаг --dry-run: показать план без создания документов.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from config import Config
from db import Db
from moysklad.client import MoySkladClient
from moysklad.entities import Entities
from notifier import Notifier
from sync.context import SyncContext
from sync.matching import SkuResolver
from sync.orders import sync_orders
from sync.returns import sync_returns
from sync.shipments import sync_shipments
from sync.stock import check_stock
from sync.transfers import sync_transfers
from uzum.client import UzumClient

log = logging.getLogger("main")


def build_context(cfg: Config | None = None) -> SyncContext:
    cfg = cfg or Config.from_env()
    db = Db(cfg.db_path)
    uzum = UzumClient(cfg.uzum_token)
    ms = MoySkladClient(cfg.ms_token)
    entities = Entities(ms, cfg)
    shop_id = cfg.uzum_shop_id or uzum.default_shop_id()
    resolver = SkuResolver(uzum, entities, db, shop_id)
    notifier = Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    return SyncContext(
        cfg=cfg, db=db, uzum=uzum, ms=ms, entities=entities,
        resolver=resolver, notifier=notifier, shop_id=shop_id,
    )


def cmd_init(ctx: SyncContext) -> None:
    shops = ctx.uzum.shops()
    log.info("Uzum: магазины %s, работаем с shopId=%s", shops, ctx.shop_id)
    org = ctx.entities.organization()
    log.info("МойСклад: организация «%s»", org.get("name"))
    log.info("Склад-источник: «%s»", ctx.entities.store_source().get("name"))
    log.info("Склад Uzum: «%s»", ctx.entities.store_uzum().get("name"))
    log.info("Контрагент: «%s»", ctx.entities.agent().get("name"))
    ctx.entities.attributes("customerorder")
    ctx.entities.attributes("demand")
    ctx.entities.order_states()
    log.info("Доп. поля и статусы готовы. Инициализация успешна.")


def run_cycle(ctx: SyncContext, dry_run: bool) -> None:
    sync_orders(ctx, dry_run)
    sync_shipments(ctx, dry_run)
    sync_returns(ctx, dry_run)
    sync_transfers(ctx, dry_run)


def cmd_run(ctx: SyncContext, dry_run: bool) -> None:
    next_orders = 0.0
    next_transfers = 0.0
    next_stock = 0.0
    log.info(
        "Цикл запущен: заказы каждые %dс, поставки %dс, остатки %dс",
        ctx.cfg.orders_interval, ctx.cfg.transfers_interval, ctx.cfg.stock_interval,
    )
    while True:
        now = time.time()
        try:
            if now >= next_orders:
                sync_orders(ctx, dry_run)
                sync_shipments(ctx, dry_run)
                sync_returns(ctx, dry_run)
                next_orders = now + ctx.cfg.orders_interval
            if now >= next_transfers:
                sync_transfers(ctx, dry_run)
                next_transfers = now + ctx.cfg.transfers_interval
            if now >= next_stock:
                check_stock(ctx)
                next_stock = now + ctx.cfg.stock_interval
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Ошибка цикла синхронизации")
            ctx.notifier.send("🔥 Uzum-sync: ошибка цикла синхронизации, см. лог")
        time.sleep(5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синхронизация Uzum Market -> МойСклад")
    parser.add_argument(
        "command",
        choices=[
            "init", "sync-orders", "sync-shipments", "sync-returns",
            "sync-transfers", "check-stock", "sync-all", "run", "dry-run",
        ],
    )
    parser.add_argument("--dry-run", action="store_true", help="не создавать документы")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    ctx = build_context()
    dry_run = args.dry_run or args.command == "dry-run"
    try:
        if args.command == "init":
            cmd_init(ctx)
        elif args.command == "sync-orders":
            sync_orders(ctx, dry_run)
        elif args.command == "sync-shipments":
            sync_shipments(ctx, dry_run)
        elif args.command == "sync-returns":
            sync_returns(ctx, dry_run)
        elif args.command == "sync-transfers":
            sync_transfers(ctx, dry_run)
        elif args.command == "check-stock":
            check_stock(ctx)
        elif args.command in ("sync-all", "dry-run"):
            run_cycle(ctx, dry_run)
        elif args.command == "run":
            cmd_run(ctx, dry_run)
    except KeyboardInterrupt:
        log.info("Остановлено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
