"""Notifier de Telegram (§11.7).

Envía texto (o una Suggestion) al chat configurado vía la Bot API. Ahí termina el
sistema: el usuario decide y ejecuta manualmente en IBKR.
"""

from __future__ import annotations

import httpx

from ..config import Settings
from ..logging import get_logger
from ..reasoning import Suggestion

logger = get_logger(__name__)


class NotifierError(RuntimeError):
    """Error al enviar una notificación."""


class TelegramNotifier:
    """Cliente mínimo del Bot API de Telegram para mandar mensajes."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise NotifierError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID no configurados.")
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._client = client or httpx.Client(
            base_url=settings.telegram_base_url,
            timeout=httpx.Timeout(10.0),
        )

    def send(self, text: str) -> None:
        """Envía un mensaje de texto al chat configurado."""
        try:
            resp = self._client.post(
                f"/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise NotifierError(f"Fallo enviando a Telegram: {exc}") from exc
        if not data.get("ok", False):
            raise NotifierError(f"Telegram rechazó el mensaje: {data!r}")
        logger.info("Notificación enviada a Telegram.")

    def notify(self, suggestion: Suggestion) -> None:
        """Envía el texto de una Suggestion."""
        self.send(suggestion.text)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramNotifier:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
