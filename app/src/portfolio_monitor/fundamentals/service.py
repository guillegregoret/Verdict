"""FundamentalsService: refresca snapshots de fundamentals (§11.5).

Recorre un conjunto de tickers, los trae del provider y persiste un snapshot.
Un ticker que falla no aborta el refresh. `from_engine()` cablea el repo.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine

from ..data.edgar_fmp import Fundamentals, FundamentalsError, FundamentalsProvider
from ..db.repositories import FundamentalsRepository
from ..logging import get_logger

logger = get_logger(__name__)


class FundamentalsSink(Protocol):
    def insert_snapshot(self, fundamentals: Fundamentals) -> None: ...


class FundamentalsService:
    """Orquesta fetch de fundamentals → persistencia de snapshots."""

    def __init__(self, provider: FundamentalsProvider, repo: FundamentalsSink) -> None:
        self._provider = provider
        self._repo = repo

    @classmethod
    def from_engine(
        cls, provider: FundamentalsProvider, engine: Engine
    ) -> FundamentalsService:
        """Construye el service cableando el repo concreto sobre `engine`."""
        return cls(provider=provider, repo=FundamentalsRepository(engine))

    def refresh(self, tickers: list[str]) -> int:
        """Refresca los tickers dados. Devuelve cuántos snapshots se guardaron."""
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
