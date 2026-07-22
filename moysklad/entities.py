"""Справочные сущности МойСклад: организация, контрагент, склады,
доп. поля, статусы заказа, поиск товара по штрихкоду/артикулу."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import Config
from moysklad.client import MoySkladClient, MoySkladError, escape_filter

log = logging.getLogger(__name__)

# Доп. поля, создаваемые на заказе покупателя и отгрузке (§6 docs/mapping.md)
ATTRIBUTES: list[tuple[str, str]] = [
    ("ID заказа Uzum", "string"),
    ("Дата доставки", "time"),
    ("Комиссия", "double"),
    ("Логистика", "double"),
]

STATE_PROCESSING = "Uzum: в обработке"
STATE_ISSUED = "Uzum: выкуплен"
STATE_CANCELLED = "Uzum: отменён"
STATE_RETURNED = "Uzum: возврат"

# name, stateType, color (десятичный ARGB)
ORDER_STATES: list[tuple[str, str, int]] = [
    (STATE_PROCESSING, "Regular", 10774205),
    (STATE_ISSUED, "Successful", 8825440),
    (STATE_CANCELLED, "Unsuccessful", 15280409),
    (STATE_RETURNED, "Regular", 15106326),
]

# Документы МойСклад ждут время в формате "ГГГГ-ММ-ДД ЧЧ:мм:сс" по Москве (UTC+3)
_MSK = timezone(timedelta(hours=3))


def ms_moment(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=_MSK).strftime("%Y-%m-%d %H:%M:%S")


def meta_ref(obj: dict[str, Any]) -> dict[str, Any]:
    """Ссылка {meta:...} на сущность по её объекту из API."""
    return {"meta": obj["meta"]}


class Entities:
    def __init__(self, ms: MoySkladClient, cfg: Config) -> None:
        self._ms = ms
        self._cfg = cfg
        self._cache: dict[str, Any] = {}

    # --- базовые справочники ---
    def organization(self) -> dict[str, Any]:
        if "org" not in self._cache:
            if self._cfg.organization_name:
                row = self._ms.find_one(
                    "/entity/organization", f"name={escape_filter(self._cfg.organization_name)}"
                )
                if row is None:
                    raise MoySkladError(
                        f"Организация «{self._cfg.organization_name}» не найдена"
                    )
            else:
                rows = self._ms.rows("/entity/organization")
                if not rows:
                    raise MoySkladError("В аккаунте МойСклад нет организаций")
                row = rows[0]
            self._cache["org"] = row
        return self._cache["org"]

    def agent(self) -> dict[str, Any]:
        """Единый контрагент-покупатель для заказов Uzum; создаётся при отсутствии."""
        if "agent" not in self._cache:
            name = self._cfg.agent_name
            row = self._ms.find_one("/entity/counterparty", f"name={escape_filter(name)}")
            if row is None:
                log.info("Создаю контрагента «%s»", name)
                row = self._ms.post(
                    "/entity/counterparty",
                    {"name": name, "description": "Обезличенный покупатель Uzum Market (FBO)"},
                )
            self._cache["agent"] = row
        return self._cache["agent"]

    def _store_by_id(self, store_id: str) -> dict[str, Any]:
        return self._ms.get(f"/entity/store/{store_id}")

    def _store_by_names(self, names: set[str]) -> dict[str, Any] | None:
        wanted = {n.casefold() for n in names}
        for row in self._ms.rows("/entity/store"):
            if (row.get("name") or "").casefold().strip() in wanted:
                return row
        return None

    def store_source(self) -> dict[str, Any]:
        """Физический склад «Фулфилмент Евгений»."""
        if "store_source" not in self._cache:
            if self._cfg.store_source_id:
                row = self._store_by_id(self._cfg.store_source_id)
            else:
                row = self._store_by_names({self._cfg.store_source_name})
                if row is None:
                    raise MoySkladError(
                        f"Склад «{self._cfg.store_source_name}» не найден в МойСклад"
                    )
            self._cache["store_source"] = row
        return self._cache["store_source"]

    def store_uzum(self) -> dict[str, Any]:
        """Склад маркетплейса «Uzum» (принимаем и «Узум»)."""
        if "store_uzum" not in self._cache:
            if self._cfg.store_uzum_id:
                row = self._store_by_id(self._cfg.store_uzum_id)
            else:
                row = self._store_by_names({self._cfg.store_uzum_name, "Uzum", "Узум"})
                if row is None:
                    raise MoySkladError(
                        f"Склад «{self._cfg.store_uzum_name}» не найден в МойСклад"
                    )
            self._cache["store_uzum"] = row
        return self._cache["store_uzum"]

    # --- доп. поля ---
    def attributes(self, entity_type: str) -> dict[str, dict[str, Any]]:
        """Метаданные доп. полей entity_type (создаёт недостающие). name -> attr."""
        key = f"attrs:{entity_type}"
        if key not in self._cache:
            path = f"/entity/{entity_type}/metadata/attributes"
            data = self._ms.get(path)
            existing = {r["name"]: r for r in (data.get("rows") or [])}
            missing = [
                {"name": name, "type": type_, "required": False}
                for name, type_ in ATTRIBUTES
                if name not in existing
            ]
            if missing:
                log.info(
                    "Создаю доп. поля %s: %s",
                    entity_type, ", ".join(a["name"] for a in missing),
                )
                created = self._ms.post(path, missing)
                rows = created if isinstance(created, list) else [created]
                for r in rows:
                    existing[r["name"]] = r
            self._cache[key] = existing
        return self._cache[key]

    def attr_values(
        self, entity_type: str, values: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Собрать массив attributes для тела документа (None пропускается)."""
        attrs = self.attributes(entity_type)
        out = []
        for name, value in values.items():
            if value is None or name not in attrs:
                continue
            out.append({"meta": attrs[name]["meta"], "value": value})
        return out

    # --- статусы заказа покупателя ---
    def order_states(self) -> dict[str, dict[str, Any]]:
        if "states" not in self._cache:
            meta = self._ms.get("/entity/customerorder/metadata")
            existing = {s["name"]: s for s in (meta.get("states") or [])}
            for name, state_type, color in ORDER_STATES:
                if name in existing:
                    continue
                log.info("Создаю статус заказа «%s»", name)
                try:
                    created = self._ms.post(
                        "/entity/customerorder/metadata/states",
                        {"name": name, "stateType": state_type, "color": color},
                    )
                except MoySkladError as exc:
                    # финальный положительный/отрицательный статус может быть
                    # только один (code 3007) — создаём как обычный
                    if "3007" not in str(exc) or state_type == "Regular":
                        raise
                    log.warning("Статус «%s»: тип %s занят, создаю как Regular", name, state_type)
                    created = self._ms.post(
                        "/entity/customerorder/metadata/states",
                        {"name": name, "stateType": "Regular", "color": color},
                    )
                existing[name] = created
            self._cache["states"] = existing
        return self._cache["states"]

    def order_state_ref(self, name: str) -> dict[str, Any]:
        return meta_ref(self.order_states()[name])

    # --- поиск товара ---
    def find_assortment(
        self, sku_title: str, barcode: str | None = None
    ) -> dict[str, Any] | None:
        """Поиск позиции: штрихкод -> артикул товара -> код модификации.

        Возвращает {href, type, name, matched_by} либо None.
        """
        if barcode:
            row = self._ms.find_one("/entity/assortment", f"barcode={escape_filter(barcode)}")
            if row:
                return self._found(row, "barcode")
        if sku_title:
            row = self._ms.find_one("/entity/product", f"article={escape_filter(sku_title)}")
            if row:
                return self._found(row, "article")
            row = self._ms.find_one("/entity/variant", f"code={escape_filter(sku_title)}")
            if row:
                return self._found(row, "variant_code")
        return None

    @staticmethod
    def _found(row: dict[str, Any], matched_by: str) -> dict[str, Any]:
        return {
            "href": row["meta"]["href"],
            "type": row["meta"]["type"],
            "name": row.get("name") or "",
            "matched_by": matched_by,
        }
