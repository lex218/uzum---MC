"""Конфигурация сервиса синхронизации Uzum -> МойСклад."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

UZUM_BASE_URL = "https://api-seller.uzum.uz/api/seller-openapi"
MS_BASE_URL = "https://api.moysklad.ru/api/remap/1.2"

# МойСклад хранит цены в минорных единицах (1/100 валюты аккаунта, у нас — сумы).
PRICE_MINOR_UNITS = 100


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    uzum_token: str
    ms_token: str
    uzum_shop_id: int | None = None
    organization_name: str | None = None
    agent_name: str = "Uzum Market (покупатель)"
    store_source_id: str | None = None
    store_uzum_id: str | None = None
    store_source_name: str = "Фулфилмент Евгений"
    store_uzum_name: str = "Uzum"
    orders_interval: int = 300
    transfers_interval: int = 600
    stock_interval: int = 3600
    orders_overlap_hours: int = 48
    orders_initial_days: int = 1
    return_window_days: int = 30
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    db_path: str = "sync.sqlite3"

    @classmethod
    def from_env(cls) -> "Config":
        uzum_token = os.getenv("UZUM_TOKEN")
        ms_token = os.getenv("MS_TOKEN")
        if not uzum_token:
            raise RuntimeError("UZUM_TOKEN не задан (см. .env.example)")
        if not ms_token:
            raise RuntimeError("MS_TOKEN не задан (см. .env.example)")
        shop = os.getenv("UZUM_SHOP_ID")
        return cls(
            uzum_token=uzum_token,
            ms_token=ms_token,
            uzum_shop_id=int(shop) if shop else None,
            organization_name=os.getenv("MS_ORGANIZATION_NAME") or None,
            agent_name=os.getenv("MS_AGENT_NAME") or "Uzum Market (покупатель)",
            store_source_id=os.getenv("MS_STORE_SOURCE_ID") or None,
            store_uzum_id=os.getenv("MS_STORE_UZUM_ID") or None,
            store_source_name=os.getenv("MS_STORE_SOURCE_NAME") or "Фулфилмент Евгений",
            store_uzum_name=os.getenv("MS_STORE_UZUM_NAME") or "Uzum",
            orders_interval=_int("ORDERS_INTERVAL", 300),
            transfers_interval=_int("TRANSFERS_INTERVAL", 600),
            stock_interval=_int("STOCK_INTERVAL", 3600),
            orders_overlap_hours=_int("ORDERS_OVERLAP_HOURS", 48),
            orders_initial_days=_int("ORDERS_INITIAL_DAYS", 1),
            return_window_days=_int("RETURN_WINDOW_DAYS", 30),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            db_path=os.getenv("DB_PATH") or "sync.sqlite3",
        )
