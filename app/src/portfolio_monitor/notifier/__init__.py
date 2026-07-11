"""Notifier: bot de Telegram (§11.7).

Ahí termina el sistema: el usuario decide y ejecuta manualmente en IBKR.
"""

from .telegram import NotifierError, TelegramNotifier

__all__ = ["NotifierError", "TelegramNotifier"]
