"""EarningsService: fetch del calendario de earnings → upsert en Postgres (§5).

Recorre los tickers habilitados, trae sus earnings próximos de Finnhub y los
upsertea. Un ticker que falla no aborta el refresh. Pensado para correr
throttleado en el scheduler (las fechas cambian lento). `run_once()` para
encajar con el patrón del loop.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine

from ..data.finnhub import EarningsEvent, FinnhubError
from ..db.repositories import EarningsRepository, TickerConfigRepository
from ..logging import get_logger

logger = get_logger(__name__)


class EarningsProvider(Protocol):
    def fetch_upcoming(self, ticker: str, horizon_days: int) -> list[EarningsEvent]: ...


class TickerSource(Protocol):
    def enabled_tickers(self) -> list[str]: ...


class EarningsSink(Protocol):
    def upsert_many(self, events: list[EarningsEvent]) -> int: ...


class EarningsService:
    """Orquesta fetch del calendario de earnings → persistencia."""

    def __init__(
        self,
        provider: EarningsProvider,
        repo: EarningsSink,
        tickers: TickerSource,
        horizon_days: int = 120,
    ) -> None:
        self._provider = provider
        self._repo = repo
        self._tickers = tickers
        self._horizon_days = horizon_days

    @classmethod
    def from_engine(
        cls, provider: EarningsProvider, engine: Engine, horizon_days: int = 120
    ) -> EarningsService:
        return cls(
            provider=provider,
            repo=EarningsRepository(engine),
            tickers=TickerConfigRepository(engine),
            horizon_days=horizon_days,
        )

    def run_once(self) -> int:
        """Refresca los earnings de los tickers habilitados. Devuelve cuántos upserteó."""
        events: list[EarningsEvent] = []
        for ticker in self._tickers.enabled_tickers():
            try:
                events.extend(self._provider.fetch_upcoming(ticker, self._horizon_days))
            except FinnhubError as exc:
                logger.warning("Earnings %s falló: %s", ticker, exc)
        saved = self._repo.upsert_many(events)
        logger.info("Refresh de earnings: %d eventos upserteados.", saved)
        return saved
