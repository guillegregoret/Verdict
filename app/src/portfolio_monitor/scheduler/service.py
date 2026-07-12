"""Scheduler: loop principal del monolito (§2).

Cada tick: pollea precios (escribe a Postgres) y corre el AlertPipeline
(detecta caídas → razona → notifica). Un solo loop secuencial — la detección lee
los precios que el poller acaba de escribir, así que no hace falta concurrencia.
"""

from __future__ import annotations

import time
from typing import Protocol

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)


class PollerLike(Protocol):
    def poll_once(self) -> int: ...


class PipelineLike(Protocol):
    def run_once(self) -> int: ...


class PingerLike(Protocol):
    def ping(self, success: bool = ...) -> None: ...


class HoldingsSyncLike(Protocol):
    def run_once(self) -> int: ...


class Scheduler:
    """Orquesta poller + pipeline en un loop periódico."""

    def __init__(
        self,
        settings: Settings,
        poller: PollerLike,
        pipeline: PipelineLike,
        pinger: PingerLike | None = None,
        holdings_sync: HoldingsSyncLike | None = None,
    ) -> None:
        self._settings = settings
        self._poller = poller
        self._pipeline = pipeline
        self._pinger = pinger
        self._holdings_sync = holdings_sync
        self._ticks = 0

    def tick(self) -> None:
        """Un ciclo: sync de holdings (throttleado) → pollea precios → evalúa/notifica."""
        self._maybe_sync_holdings()
        self._poller.poll_once()
        self._pipeline.run_once()

    def _maybe_sync_holdings(self) -> None:
        """Sincroniza holdings 1 cada N ticks (config). Aislado: nunca tumba el tick."""
        every = self._settings.holdings_sync_every_ticks
        if self._holdings_sync is None or every <= 0:
            return
        if self._ticks % every == 0:  # primer tick incluido → puebla al arrancar
            try:
                self._holdings_sync.run_once()
            except Exception:  # noqa: BLE001 - el sync no debe abortar poll/pipeline
                logger.exception("Holdings sync falló (se continúa con el tick).")
        self._ticks += 1

    def run_forever(self) -> None:
        """Loop principal. Un tick que falla no mata el proceso.

        Pinga el dead-man's switch (§9) con éxito/fallo según el resultado del tick.
        """
        interval = self._settings.poll_interval_seconds
        logger.info("Scheduler arrancado (intervalo=%ss).", interval)
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - un tick no debe tumbar el proceso
                logger.exception("Tick del scheduler falló.")
                self._safe_ping(success=False)
            else:
                self._safe_ping(success=True)
            time.sleep(interval)

    def _safe_ping(self, success: bool) -> None:
        if self._pinger is not None:
            self._pinger.ping(success=success)
