"""Bot de Telegram interactivo (§5): consultar estado on-demand, solo lectura.

Seguridad: long-polling (sin puertos abiertos), allowlist por user id personal
(fail-closed: bloqueado hasta habilitar tu id), comandos read-only.
"""

from .bot import TelegramBot
from .commands import CommandRouter

__all__ = ["CommandRouter", "TelegramBot"]
