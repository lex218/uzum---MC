"""Общий контекст sync-модулей."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import Config
from db import Db
from notifier import Notifier


@dataclass
class SyncContext:
    cfg: Config
    db: Db
    uzum: Any  # UzumClient
    ms: Any  # MoySkladClient
    entities: Any  # Entities
    resolver: Any  # SkuResolver
    notifier: Notifier
    shop_id: int
