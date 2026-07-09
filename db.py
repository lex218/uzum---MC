"""SQLite-хранилище соответствий ID и состояния синхронизации (идемпотентность)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    uzum_order_id   TEXT PRIMARY KEY,
    ms_order_id     TEXT,
    ms_order_href   TEXT,
    status          TEXT NOT NULL,  -- created | shipped | cancelled | returned | skipped
    ms_demand_id    TEXT,
    ms_demand_href  TEXT,
    first_seen_ms   INTEGER,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    uzum_item_id        TEXT PRIMARY KEY,
    uzum_order_id       TEXT NOT NULL,
    sku_title           TEXT,
    ms_assortment_href  TEXT,
    ms_assortment_type  TEXT,
    price               INTEGER,            -- цена позиции, сумы
    qty_synced          INTEGER,            -- количество в позиции заказа МС
    status              TEXT,
    date_issued_ms      INTEGER,
    amount_returns      INTEGER DEFAULT 0,
    return_cause        TEXT,
    returns_synced      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(uzum_order_id);
CREATE TABLE IF NOT EXISTS invoices (
    uzum_invoice_id INTEGER PRIMARY KEY,
    invoice_number  TEXT,
    ms_move_id      TEXT,
    ms_move_href    TEXT,
    ms_loss_id      TEXT,
    sent_json       TEXT,
    accepted_json   TEXT,
    reconciled      INTEGER DEFAULT 0,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS sku_cache (
    key         TEXT PRIMARY KEY,
    ms_href     TEXT,
    ms_type     TEXT,
    matched_by  TEXT,
    name        TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Db:
    def __init__(self, path: str = "sync.sqlite3") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- kv ---
    def get_kv(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    # --- orders ---
    def get_order(self, uzum_order_id: int | str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM orders WHERE uzum_order_id=?", (str(uzum_order_id),)
        ).fetchone()
        return dict(row) if row else None

    def upsert_order(
        self,
        uzum_order_id: int | str,
        status: str,
        ms_order_id: str | None = None,
        ms_order_href: str | None = None,
        first_seen_ms: int | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO orders(uzum_order_id, ms_order_id, ms_order_href, status, first_seen_ms, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(uzum_order_id) DO UPDATE SET
                 ms_order_id=COALESCE(excluded.ms_order_id, ms_order_id),
                 ms_order_href=COALESCE(excluded.ms_order_href, ms_order_href),
                 status=excluded.status, updated_at=excluded.updated_at""",
            (str(uzum_order_id), ms_order_id, ms_order_href, status, first_seen_ms, _now()),
        )
        self._conn.commit()

    def set_order_status(self, uzum_order_id: int | str, status: str) -> None:
        self._conn.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE uzum_order_id=?",
            (status, _now(), str(uzum_order_id)),
        )
        self._conn.commit()

    def set_order_demand(
        self, uzum_order_id: int | str, demand_id: str, demand_href: str
    ) -> None:
        self._conn.execute(
            "UPDATE orders SET ms_demand_id=?, ms_demand_href=?, status='shipped', updated_at=? "
            "WHERE uzum_order_id=?",
            (demand_id, demand_href, _now(), str(uzum_order_id)),
        )
        self._conn.commit()

    def orders_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(statuses))
        rows = self._conn.execute(
            f"SELECT * FROM orders WHERE status IN ({marks})", statuses
        ).fetchall()
        return [dict(r) for r in rows]

    def oldest_open_order_ms(self) -> int | None:
        """Дата самого старого незакрытого заказа — нижняя граница окна опроса."""
        row = self._conn.execute(
            "SELECT MIN(first_seen_ms) m FROM orders WHERE status IN ('created','shipped','returned')"
        ).fetchone()
        return row["m"] if row and row["m"] is not None else None

    # --- order items ---
    def get_item(self, uzum_item_id: int | str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM order_items WHERE uzum_item_id=?", (str(uzum_item_id),)
        ).fetchone()
        return dict(row) if row else None

    def order_items(self, uzum_order_id: int | str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM order_items WHERE uzum_order_id=?", (str(uzum_order_id),)
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_item(
        self,
        uzum_item_id: int | str,
        uzum_order_id: int | str,
        sku_title: str,
        ms_assortment_href: str | None,
        ms_assortment_type: str | None,
        price: int,
        qty_synced: int,
        status: str,
        date_issued_ms: int | None,
        amount_returns: int = 0,
        return_cause: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO order_items(uzum_item_id, uzum_order_id, sku_title,
                 ms_assortment_href, ms_assortment_type, price, qty_synced, status,
                 date_issued_ms, amount_returns, return_cause)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(uzum_item_id) DO UPDATE SET
                 sku_title=excluded.sku_title,
                 ms_assortment_href=COALESCE(excluded.ms_assortment_href, ms_assortment_href),
                 ms_assortment_type=COALESCE(excluded.ms_assortment_type, ms_assortment_type),
                 price=excluded.price, qty_synced=excluded.qty_synced,
                 status=excluded.status, date_issued_ms=excluded.date_issued_ms,
                 amount_returns=excluded.amount_returns,
                 return_cause=COALESCE(excluded.return_cause, return_cause)""",
            (
                str(uzum_item_id), str(uzum_order_id), sku_title, ms_assortment_href,
                ms_assortment_type, price, qty_synced, status, date_issued_ms,
                amount_returns, return_cause,
            ),
        )
        self._conn.commit()

    def set_item_qty(self, uzum_item_id: int | str, qty: int) -> None:
        self._conn.execute(
            "UPDATE order_items SET qty_synced=? WHERE uzum_item_id=?",
            (qty, str(uzum_item_id)),
        )
        self._conn.commit()

    def set_item_returns(self, uzum_item_id: int | str, returns_synced: int) -> None:
        self._conn.execute(
            "UPDATE order_items SET returns_synced=? WHERE uzum_item_id=?",
            (returns_synced, str(uzum_item_id)),
        )
        self._conn.commit()

    def items_with_pending_returns(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT i.*, o.ms_demand_href, o.ms_order_href FROM order_items i "
            "JOIN orders o ON o.uzum_order_id = i.uzum_order_id "
            "WHERE o.ms_order_id IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- invoices ---
    def get_invoice(self, uzum_invoice_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM invoices WHERE uzum_invoice_id=?", (uzum_invoice_id,)
        ).fetchone()
        return dict(row) if row else None

    def add_invoice(
        self,
        uzum_invoice_id: int,
        invoice_number: str,
        ms_move_id: str,
        ms_move_href: str,
        sent: dict[str, int],
    ) -> None:
        self._conn.execute(
            """INSERT INTO invoices(uzum_invoice_id, invoice_number, ms_move_id, ms_move_href,
                 sent_json, updated_at) VALUES(?,?,?,?,?,?)""",
            (uzum_invoice_id, invoice_number, ms_move_id, ms_move_href, json.dumps(sent), _now()),
        )
        self._conn.commit()

    def pending_invoice_ids(self) -> set[int]:
        rows = self._conn.execute(
            "SELECT uzum_invoice_id FROM invoices WHERE reconciled=0"
        ).fetchall()
        return {r["uzum_invoice_id"] for r in rows}

    def mark_invoice_reconciled(
        self, uzum_invoice_id: int, accepted: dict[str, int], ms_loss_id: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE invoices SET reconciled=1, accepted_json=?, ms_loss_id=?, updated_at=? "
            "WHERE uzum_invoice_id=?",
            (json.dumps(accepted), ms_loss_id, _now(), uzum_invoice_id),
        )
        self._conn.commit()

    # --- sku cache ---
    def get_sku(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM sku_cache WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def put_sku(self, key: str, ms_href: str, ms_type: str, matched_by: str, name: str) -> None:
        self._conn.execute(
            """INSERT INTO sku_cache(key, ms_href, ms_type, matched_by, name) VALUES(?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET ms_href=excluded.ms_href, ms_type=excluded.ms_type,
                 matched_by=excluded.matched_by, name=excluded.name""",
            (key, ms_href, ms_type, matched_by, name),
        )
        self._conn.commit()

    def all_skus(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM sku_cache").fetchall()]
