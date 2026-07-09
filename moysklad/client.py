"""Клиент МойСклад JSON API 1.2.

Обязателен Accept-Encoding: gzip (иначе 415). Лимит ~45 запросов/3 сек:
на 429 ждём X-Lognex-Retry-After (мс), на 5xx — экспоненциальный backoff.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import MS_BASE_URL

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
PAGE_LIMIT = 100


class MoySkladError(Exception):
    pass


class MoySkladClient:
    def __init__(
        self,
        token: str,
        base_url: str = MS_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self._client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise MoySkladError(f"МойСклад {method} {path}: сетевая ошибка: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                retry_ms = resp.headers.get("X-Lognex-Retry-After")
                delay = int(retry_ms) / 1000 if retry_ms else float(2**attempt)
                log.warning(
                    "МойСклад %s %s: HTTP %s, повтор через %.1fс",
                    method, path, resp.status_code, delay,
                )
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise MoySkladError(
                    f"МойСклад {method} {path}: HTTP {resp.status_code}: {self._error_text(resp)}"
                )
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        raise MoySkladError(f"МойСклад {method} {path}: исчерпаны попытки")

    @staticmethod
    def _error_text(resp: httpx.Response) -> str:
        try:
            errors = resp.json().get("errors") or []
            return "; ".join(
                f"{e.get('error')} (code {e.get('code')})" for e in errors
            ) or resp.text[:300]
        except Exception:
            return resp.text[:300]

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: Any) -> Any:
        return self._request("POST", path, json=json)

    def put(self, path: str, json: Any) -> Any:
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def rows(
        self,
        path: str,
        filter_: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Все строки коллекции с постраничным обходом."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_params: dict[str, Any] = {"limit": PAGE_LIMIT, "offset": offset}
            if filter_:
                page_params["filter"] = filter_
            if params:
                page_params.update(params)
            data = self.get(path, page_params)
            rows = data.get("rows") or []
            out.extend(rows)
            if len(rows) < PAGE_LIMIT:
                return out
            offset += PAGE_LIMIT

    def find_one(self, path: str, filter_: str) -> dict[str, Any] | None:
        data = self.get(path, {"filter": filter_, "limit": 1})
        rows = data.get("rows") or []
        return rows[0] if rows else None
