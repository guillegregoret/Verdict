"""RatingsService: fetch del consenso de analistas → upsert en Postgres (§5).

Recorre los tickers habilitados, trae sus snapshots de Finnhub y los upsertea.
Un ticker que falla no aborta el refresh. Throttleado en el scheduler (el
consenso cambia lento). `run_once()` para encajar con el patrón del loop.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine

from ..data.finnhub import FinnhubError, RatingSnapshot
from ..db.repositories import RatingsRepository, TickerConfigRepository
from ..logging import get_logger

logger = get_logger(__name__)


class RatingsProvider(Protocol):
    def fetch(self, ticker: str) -> list[RatingSnapshot]: ...


class TickerSource(Protocol):
    def enabled_tickers(self) -> list[str]: ...


class RatingsSink(Protocol):
    def upsert_many(self, snapshots: list[RatingSnapshot]) -> int: ...


class RatingsService:
    """Orquesta fetch del consenso de analistas → persistencia."""

    def __init__(
        self, provider: RatingsProvider, repo: RatingsSink, tickers: TickerSource
    ) -> None:
        self._provider = provider
        self._repo = repo
        self._tickers = tickers

    @classmethod
    def from_engine(cls, provider: RatingsProvider, engine: Engine) -> RatingsService:
        return cls(
            provider=provider,
            repo=RatingsRepository(engine),
            tickers=TickerConfigRepository(engine),
        )

    def run_once(self) -> int:
        """Refresca los ratings de los tickers habilitados. Devuelve cuántos upserteó."""
        snapshots: list[RatingSnapshot] = []
        for ticker in self._tickers.enabled_tickers():
            try:
                snapshots.extend(self._provider.fetch(ticker))
            except FinnhubError as exc:
                logger.warning("Ratings %s falló: %s", ticker, exc)
        saved = self._repo.upsert_many(snapshots)
        logger.info("Refresh de ratings: %d snapshots upserteados.", saved)
        return saved
