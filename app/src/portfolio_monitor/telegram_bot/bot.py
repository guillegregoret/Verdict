"""TelegramBot: long-polling seguro del Bot API (§5, §8).

Seguridad:
- Long-polling (getUpdates): todo saliente, sin puertos abiertos (§8 deny-inbound).
- Allowlist por user id personal (TELEGRAM_ALLOWED_USER_IDS). FAIL-CLOSED: si está
  vacía, no responde ningún dato — solo /whoami (para descubrir tu id).
- Comandos read-only; el bot nunca ejecuta ni cambia nada.
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)

# Comandos que tardan (llaman a Claude): mandamos un ack antes de procesar.
_SLOW_COMMANDS = frozenset({"reevaluar", "review"})
# Límite de Telegram por mensaje (4096); dejamos margen y partimos por líneas.
_MAX_MESSAGE_CHARS = 3900


class Router(Protocol):
    def handle(self, text: str) -> str: ...


def _split_message(text: str, limit: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Parte un texto largo en trozos ≤ limit, cortando por líneas cuando puede."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # una línea sola más larga que el límite: corte duro
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _parse_ids(raw: str) -> frozenset[int]:
    """CSV de user ids → set de ints (ignora vacíos/basura)."""
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                logger.warning("TELEGRAM_ALLOWED_USER_IDS: id inválido %r, ignorado.", part)
    return frozenset(ids)


class TelegramBot:
    """Bot interactivo read-only con allowlist por user id (fail-closed)."""

    def __init__(
        self, settings: Settings, router: Router, client: httpx.Client | None = None
    ) -> None:
        self._token = settings.telegram_bot_token
        self._allowed = _parse_ids(settings.telegram_allowed_user_ids)
        self._router = router
        self._client = client or httpx.Client(
            base_url=settings.telegram_base_url, timeout=httpx.Timeout(45.0)
        )
        self._offset: int | None = None
        if not self._allowed:
            logger.warning(
                "Bot Telegram FAIL-CLOSED: TELEGRAM_ALLOWED_USER_IDS vacío. "
                "Solo responde /whoami hasta que agregues tu user id."
            )

    def _authorized(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._allowed

    def _parse(self, update: dict) -> tuple[int, int | None, str, str] | None:
        """(chat_id, user_id, cmd, text) de un update de comando, o None."""
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return None
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return None
        chat_id = msg.get("chat", {}).get("id")
        user_id = (msg.get("from") or {}).get("id")
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()
        return chat_id, user_id, cmd, text

    def response_for(self, update: dict) -> tuple[int, str] | None:
        """(chat_id, respuesta) para un update, o None si no hay que responder.

        Pura (sin I/O) para testear la autorización y el ruteo.
        """
        parsed = self._parse(update)
        if parsed is None:
            return None
        chat_id, user_id, cmd, text = parsed

        if cmd == "whoami":
            return chat_id, (
                f"Tu user id: {user_id}\nChat id: {chat_id}\n\n"
                "Agregá tu user id a TELEGRAM_ALLOWED_USER_IDS para habilitar los comandos."
            )
        if not self._authorized(user_id):
            logger.warning(
                "Bot: comando no autorizado de user_id=%s chat=%s: %s",
                user_id, chat_id, text,
            )
            if not self._allowed:
                return chat_id, (
                    "🔒 Bot bloqueado. Mandá /whoami y agregá tu user id a "
                    "TELEGRAM_ALLOWED_USER_IDS."
                )
            return None  # allowlist configurada y no estás → ignorar en silencio
        try:
            return chat_id, self._router.handle(text)
        except Exception:  # noqa: BLE001 - un comando no debe tumbar el bot
            logger.exception("Bot: error procesando %s", text)
            return chat_id, "Error procesando el comando."

    def run_forever(self) -> None:
        """Loop de long-polling. Un fallo transitorio no tumba el bot."""
        logger.info("Bot Telegram arrancado (long-polling).")
        while True:
            try:
                for update in self._get_updates():
                    self._offset = update["update_id"] + 1
                    self._process(update)
            except Exception:  # noqa: BLE001 - reintenta ante cualquier fallo
                logger.exception("Bot: poll falló; reintento en 5s.")
                time.sleep(5)

    def _process(self, update: dict) -> None:
        """Procesa un update: ack para comandos lentos, luego la respuesta."""
        parsed = self._parse(update)
        if parsed is not None:
            chat_id, user_id, cmd, _ = parsed
            if cmd in _SLOW_COMMANDS and self._authorized(user_id):
                self._reply(chat_id, "🔍 Reevaluando el portfolio… puede tardar ~30s.")
        result = self.response_for(update)
        if result is not None:
            self._reply(*result)

    def _get_updates(self) -> list[dict]:
        resp = self._client.get(
            f"/bot{self._token}/getUpdates",
            params={"offset": self._offset, "timeout": 30},
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    def _reply(self, chat_id: int, text: str) -> None:
        """Envía la respuesta, partida en varios mensajes si excede el límite."""
        for chunk in _split_message(text):
            try:
                self._client.post(
                    f"/bot{self._token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk},
                )
            except httpx.HTTPError as exc:
                logger.warning("Bot: fallo respondiendo a %s: %s", chat_id, exc)
                return
