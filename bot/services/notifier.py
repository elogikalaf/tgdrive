from __future__ import annotations

import logging

from pyrogram import Client


logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self) -> None:
        self._client: Client | None = None

    def set_client(self, client: Client) -> None:
        self._client = client

    async def send_message(self, telegram_id: int, text: str) -> None:
        if not self._client:
            logger.warning("Telegram client is not ready; cannot notify telegram_id=%s", telegram_id)
            return
        await self._client.send_message(telegram_id, text)
