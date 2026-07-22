"""Telegram-уведомления об ошибках и расхождениях."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

DEDUP_WINDOW_S = 900  # одинаковые уведомления не чаще раза в 15 минут


class Notifier:
    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=15.0)
        self._recent: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, text: str) -> None:
        """Отправить сообщение; ошибки доставки не роняют синхронизацию."""
        now = time.monotonic()
        last = self._recent.get(text)
        if last is not None and now - last < DEDUP_WINDOW_S:
            return  # то же сообщение недавно уже уходило
        if len(self._recent) > 500:
            self._recent = {t: ts for t, ts in self._recent.items()
                            if now - ts < DEDUP_WINDOW_S}
        self._recent[text] = now
        log.warning("NOTIFY: %s", text)
        if not self.enabled:
            return
        try:
            resp = self._client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text[:4000]},
            )
            if resp.status_code != 200:
                log.error("Telegram: HTTP %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            log.error("Telegram недоступен: %s", exc)
