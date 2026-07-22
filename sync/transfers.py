"""Поставка (накладная) Uzum -> Перемещение «Фулфилмент Евгений» -> «Uzum».

Новая накладная — перемещение на отправленное количество (quantityToStock).
После завершения приёмки (dateAccepted/ACCEPTED) сверяем принятое с
отправленным; при недостаче — Списание со склада «Uzum» на разницу.
Пары отправлено/принято хранятся в SQLite (invoices).
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from moysklad.entities import meta_ref
from sync.context import SyncContext
from uzum.models import Invoice, InvoiceSku

log = logging.getLogger(__name__)


def sync_transfers(ctx: SyncContext, dry_run: bool = False) -> None:
    cutoff = date.today() - timedelta(days=ctx.cfg.transfers_initial_days)
    # накладные, ждущие завершения приёмки, — их ищем и глубже отсечки
    pending = ctx.db.pending_invoice_ids()
    for invoice in ctx.uzum.invoices(ctx.shop_id):
        tracked = ctx.db.get_invoice(invoice.id) is not None
        is_old = invoice.date_created is not None and invoice.date_created < cutoff
        if is_old and not tracked:
            # список идёт от новых к старым: дальше только история
            if not pending:
                log.info("Дошли до накладных старше %s — дальше история", cutoff.isoformat())
                break
            continue
        try:
            _process_invoice(ctx, invoice, dry_run)
        except Exception:
            log.exception("Ошибка обработки поставки %s", invoice.number)
            ctx.notifier.send(f"⚠️ Uzum-sync: ошибка обработки поставки №{invoice.number}")
        pending.discard(invoice.id)
    if pending:
        log.warning("Накладные из БД не найдены в выдаче Uzum: %s", sorted(pending))


def _process_invoice(ctx: SyncContext, invoice: Invoice, dry_run: bool) -> None:
    rec = ctx.db.get_invoice(invoice.id)
    if rec is None:
        if invoice.status in ("CANCELLED", "CANCELED"):
            return
        _create_move(ctx, invoice, dry_run)
    elif not rec["reconciled"]:
        if invoice.status in ("CANCELLED", "CANCELED"):
            # накладная отменена уже после создания перемещения:
            # закрываем запись, товародвижение исправляется руками
            if not dry_run:
                ctx.db.mark_invoice_reconciled(invoice.id, {}, None)
            ctx.notifier.send(
                f"⚠️ Uzum-sync: поставка №{invoice.number} отменена в Uzum после "
                f"создания перемещения — удалите/сторнируйте перемещение "
                f"UZ-INV-{invoice.number} в МойСклад вручную."
            )
            return
        if invoice.acceptance_finished:
            _reconcile(ctx, rec, invoice, dry_run)


def _positions_for(ctx: SyncContext, skus: list[InvoiceSku], invoice: Invoice, qty_key: str) -> list[dict] | None:
    positions = []
    for sku in skus:
        qty = getattr(sku, qty_key)
        if qty <= 0:
            continue
        found = ctx.resolver.resolve(sku.sku_title, sku_id=sku.sku_id)
        if found is None:
            ctx.notifier.send(
                f"❌ Uzum-sync: поставка №{invoice.number} не обработана — SKU "
                f"«{sku.sku_title}» ({sku.product_title}) не найден в МойСклад."
            )
            return None
        positions.append(
            {
                "assortment": {
                    "meta": {
                        "href": found["href"],
                        "type": found["type"],
                        "mediaType": "application/json",
                    }
                },
                "quantity": qty,
            }
        )
    return positions


def _create_move(ctx: SyncContext, invoice: Invoice, dry_run: bool) -> None:
    skus = ctx.uzum.invoice_skus(ctx.shop_id, invoice.id)
    if not skus:
        log.info("Поставка №%s без позиций — пропуск", invoice.number)
        return
    positions = _positions_for(ctx, skus, invoice, "to_stock")
    if positions is None:
        return
    if dry_run:
        log.info(
            "[dry-run] Создал бы перемещение по поставке №%s: %d позиций",
            invoice.number, len(positions),
        )
        return

    payload = {
        "name": f"UZ-INV-{invoice.number}",
        "externalCode": str(invoice.id),
        "organization": meta_ref(ctx.entities.organization()),
        "sourceStore": meta_ref(ctx.entities.store_source()),
        "targetStore": meta_ref(ctx.entities.store_uzum()),
        "description": f"Поставка Uzum №{invoice.number} (id {invoice.id})",
        "positions": positions,
    }
    existing = ctx.ms.find_one("/entity/move", f"externalCode={invoice.id}")
    move = existing or ctx.ms.post("/entity/move", payload)
    sent = {str(s.sku_id): s.to_stock for s in skus}
    ctx.db.add_invoice(invoice.id, invoice.number, move["id"], move["meta"]["href"], sent)
    log.info("Создано перемещение UZ-INV-%s (%d позиций)", invoice.number, len(positions))


def _reconcile(ctx: SyncContext, rec: dict, invoice: Invoice, dry_run: bool) -> None:
    """Сверка принятого с отправленным; недостача -> списание со склада «Uzum»."""
    skus = ctx.uzum.invoice_skus(ctx.shop_id, invoice.id)
    sent: dict[str, int] = json.loads(rec["sent_json"] or "{}")
    accepted = {str(s.sku_id): s.accepted for s in skus}

    shortages = []
    surpluses = []
    for sku in skus:
        sent_qty = sent.get(str(sku.sku_id), sku.to_stock)
        if sku.accepted < sent_qty:
            shortages.append((sku, sent_qty, sku.accepted))
        elif sku.accepted > sent_qty:
            surpluses.append((sku, sent_qty, sku.accepted))
    if surpluses:
        ctx.notifier.send(
            f"⚠️ Uzum-sync: поставка №{invoice.number} — принято больше, чем в "
            f"перемещении: "
            + "; ".join(f"«{s.sku_title}»: {a} вместо {q}" for s, q, a in surpluses)
            + ". Скорректируйте перемещение вручную."
        )

    if not shortages:
        if not dry_run:
            ctx.db.mark_invoice_reconciled(invoice.id, accepted, None)
        log.info("Поставка №%s принята без расхождений", invoice.number)
        return

    if dry_run:
        log.info(
            "[dry-run] Поставка №%s: %d расхождений, создал бы списание",
            invoice.number, len(shortages),
        )
        return

    positions = []
    lines = []
    for sku, sent_qty, acc in shortages:
        found = ctx.resolver.resolve(sku.sku_title, sku_id=sku.sku_id)
        if found is None:
            ctx.notifier.send(
                f"❌ Uzum-sync: списание по поставке №{invoice.number} — SKU "
                f"«{sku.sku_title}» не найден в МойСклад, позиция пропущена."
            )
            continue
        positions.append(
            {
                "assortment": {
                    "meta": {
                        "href": found["href"],
                        "type": found["type"],
                        "mediaType": "application/json",
                    }
                },
                "quantity": sent_qty - acc,
                "reason": f"Расхождение приёмки поставки №{invoice.number}",
            }
        )
        lines.append(f"«{sku.sku_title}»: отправлено {sent_qty}, принято {acc}")

    loss_id = None
    if positions:
        total_sent = sum(s for _, s, _ in shortages)
        total_acc = sum(a for _, _, a in shortages)
        payload = {
            "name": f"UZ-LOSS-{invoice.number}",
            "externalCode": f"loss-{invoice.id}",
            "organization": meta_ref(ctx.entities.organization()),
            "store": meta_ref(ctx.entities.store_uzum()),
            "description": (
                f"Расхождение приёмки поставки №{invoice.number}: "
                f"отправлено {total_sent}, принято {total_acc}. "
                + "; ".join(lines)
            )[:4000],
            "positions": positions,
        }
        existing = ctx.ms.find_one("/entity/loss", f"externalCode=loss-{invoice.id}")
        loss = existing or ctx.ms.post("/entity/loss", payload)
        loss_id = loss["id"]

    ctx.db.mark_invoice_reconciled(invoice.id, accepted, loss_id)
    ctx.notifier.send(
        f"📦 Uzum-sync: поставка №{invoice.number} принята с недостачей. "
        + "; ".join(lines)
        + (". Создано списание со склада «Uzum»." if loss_id else "")
    )
    log.info("Поставка №%s сверена, недостач: %d", invoice.number, len(shortages))
