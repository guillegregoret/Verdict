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


class Scheduler:
    """Orquesta poller + pipeline en un loop periódico."""

    def __init__(
        self, settings: Settings, poller: PollerLike, pipeline: PipelineLike
    ) -> None:
        self._settings = settings
        self._poller = poller
        self._pipeline = pipeline

    def tick(self) -> None:
        """Un ciclo: pollea precios y luego evalúa/notifica."""
        self._poller.poll_once()
        self._pipeline.run_once()

    def run_forever(self) -> None:
        """Loop principal. Un tick que falla no mata el proceso."""
        interval = self._settings.poll_interval_seconds
        logger.info("Scheduler arrancado (intervalo=%ss).", interval)
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - un tick no debe tumbar el proceso
                logger.exception("Tick del scheduler falló.")
            time.sleep(interval)
