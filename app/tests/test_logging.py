"""Tests de configuración de logging (redacción de secretos en URLs)."""

from __future__ import annotations

import logging

from portfolio_monitor import logging as plog


def test_setup_logging_silences_httpx_to_avoid_token_leak() -> None:
    # httpx loguea a INFO la URL con ?token=… (Finnhub) y /bot<token>/ (Telegram):
    # se baja a WARNING para que no queden secretos en docker logs.
    plog._CONFIGURED = False  # forzar reconfiguración (idempotente en runtime)
    try:
        plog.setup_logging("INFO")
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
    finally:
        plog._CONFIGURED = True
