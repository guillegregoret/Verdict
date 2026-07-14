"""FundamentalsService: fetch de fundamentals → snapshots en Postgres (§11.5).

Dos modos de uso:

- `refresh(tickers)`: barrido batch (trae y persiste un snapshot por ticker).
- `latest(ticker)`: lo que usa el AlertPipeline al gatillar (§5.3). Devuelve el
  último snapshot; si falta o quedó viejo (más que `max_age_hours`), lo refetchea
  del provider y lo guarda. Best-effort: si el provider falla, cae al snapshot
  previo (o None) sin romper la alerta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..data.edgar_fmp import Fundamentals, FundamentalsError, FundamentalsProvider
from ..db.repositories import FundamentalsRepository, FundamentalsRow
from ..logging import get_logger

logger = get_logger(__name__)


class FundamentalsStore(Protocol):
    def insert_snapshot(self, fundamentals: Fundamentals) -> None: ...
    def latest(self, ticker: str) -> FundamentalsRow | None: ...


class TickerSource(Protocol):
    def enabled_tickers(self) -> list[str]: ...


class FundamentalsService:
    """Orquesta fetch de fundamentals → persistencia + lectura con freshness."""

    def __init__(
        self,
        provider: FundamentalsProvider,
        repo: FundamentalsStore,
        max_age_hours: int = 24,
    ) -> None:
        self._provider = provider
        self._repo = repo
        self._max_age = timedelta(hours=max_age_hours)

    @classmethod
    def from_engine(
        cls, provider: FundamentalsProvider, engine: Engine, max_age_hours: int = 24
    ) -> FundamentalsService:
        """Construye el service cableando el repo concreto sobre `engine`."""
        return cls(
            provider=provider,
            repo=FundamentalsRepository(engine),
            max_age_hours=max_age_hours,
        )

    def latest(self, ticker: str, now: datetime | None = None) -> FundamentalsRow | None:
        """Último snapshot; refetchea del provider si falta o está viejo (§5.3).

        Best-effort: un fallo del provider no rompe la alerta, cae a lo previo.
        Cumple el protocolo `FundamentalsReader` que consume el AlertPipeline.
        """
        now = now or datetime.now(UTC)
        current = self._repo.latest(ticker)
        if current is not None and now - current.ts < self._max_age:
            return current  # snapshot fresco: no molestamos a FMP

        try:
            fresh = self._provider.fetch(ticker)
        except FundamentalsError as exc:
            logger.warning(
                "Fundamentals %s: refresh falló (%s); uso el snapshot previo.",
                ticker,
                exc,
            )
            return current
        if fresh is None:
            logger.info("Sin fundamentals nuevos para %s: uso lo previo.", ticker)
            return current

        self._repo.insert_snapshot(fresh)
        return self._repo.latest(ticker)

    def refresh(self, tickers: list[str]) -> int:
        """Refresca los tickers dados (batch). Devuelve cuántos snapshots se guardaron."""
        saved = 0
        for ticker in tickers:
            try:
                fundamentals = self._provider.fetch(ticker)
            except FundamentalsError as exc:
                logger.warning("Fundamentals %s falló: %s", ticker, exc)
                continue
            if fundamentals is None:
                logger.info("Sin fundamentals para %s: se omite.", ticker)
                continue
            self._repo.insert_snapshot(fundamentals)
            saved += 1
        logger.info("Refresh de fundamentals: %d/%d guardados.", saved, len(tickers))
        return saved


class FundamentalsRefreshService:
    """Refresca los fundamentals de los tickers habilitados (§5.3).

    Construye el historial de snapshots que el FundamentalsMonitor compara para
    detectar deterioro. Pensado para correr throttleado en el scheduler (los
    fundamentals cambian lento). `run_once()` para encajar con el patrón del loop.
    """

    def __init__(
        self, service: FundamentalsService, tickers: TickerSource
    ) -> None:
        self._service = service
        self._tickers = tickers

    def run_once(self) -> int:
        return self._service.refresh(self._tickers.enabled_tickers())
