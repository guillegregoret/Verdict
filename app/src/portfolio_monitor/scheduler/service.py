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


class PeriodicTask(Protocol):
    def run_once(self) -> int: ...


class WeeklyDigestLike(Protocol):
    def run(self) -> None: ...


class Scheduler:
    """Orquesta poller + pipeline en un loop periódico."""

    def __init__(
        self,
        settings: Settings,
        poller: PollerLike,
        pipeline: PipelineLike,
        pinger: PingerLike | None = None,
        holdings_sync: PeriodicTask | None = None,
        fundamentals_refresh: PeriodicTask | None = None,
        earnings_refresh: PeriodicTask | None = None,
        ratings_refresh: PeriodicTask | None = None,
        weekly_digest: WeeklyDigestLike | None = None,
    ) -> None:
        self._settings = settings
        self._poller = poller
        self._pipeline = pipeline
        self._pinger = pinger
        self._holdings_sync = holdings_sync
        self._fundamentals_refresh = fundamentals_refresh
        self._earnings_refresh = earnings_refresh
        self._ratings_refresh = ratings_refresh
        self._weekly_digest = weekly_digest
        self._ticks = 0

    def tick(self) -> None:
        """Un ciclo: sync holdings + refresh fundamentals (throttleados) →
        pollea precios → evalúa/notifica."""
        self._maybe_run(
            self._holdings_sync,
            self._settings.holdings_sync_every_ticks,
            "Holdings sync",
        )
        self._maybe_run(
            self._fundamentals_refresh,
            self._settings.fundamentals_refresh_every_ticks,
            "Fundamentals refresh",
        )
        self._maybe_run(
            self._earnings_refresh,
            self._settings.earnings_refresh_every_ticks,
            "Earnings refresh",
        )
        self._maybe_run(
            self._ratings_refresh,
            self._settings.ratings_refresh_every_ticks,
            "Ratings refresh",
        )
        self._poller.poll_once()
        self._pipeline.run_once()
        self._maybe_digest()
        self._ticks += 1

    def _maybe_digest(self) -> None:
        """Avisos semanales (lunes/viernes). El runner decide si toca y deduplica."""
        if self._weekly_digest is None:
            return
        try:
            self._weekly_digest.run()
        except Exception:  # noqa: BLE001 - un digest no debe abortar el tick
            logger.exception("Weekly digest falló (se continúa con el tick).")

    def _maybe_run(self, task: PeriodicTask | None, every: int, label: str) -> None:
        """Corre `task` 1 cada N ticks (config). Aislado: nunca tumba el tick."""
        if task is None or every <= 0:
            return
        if self._ticks % every == 0:  # primer tick incluido → corre al arrancar
            try:
                task.run_once()
            except Exception:  # noqa: BLE001 - un job periódico no aborta el tick
                logger.exception("%s falló (se continúa con el tick).", label)

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
