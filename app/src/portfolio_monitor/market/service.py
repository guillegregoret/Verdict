"""MarketContextService: arma el `MarketSnapshot` de la ventana (best-effort).

Read-only sobre la DB. Reusa la serie de precios ya poblada por el poller (los
benchmarks se traen en el mismo barrido). Un fallo nunca frena una alerta: el
caller trata el snapshot como enriquecimiento opcional.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..db.repositories import (
    Benchmark,
    BenchmarksRepository,
    HoldingsRepository,
    PriceRepository,
)
from ..logging import get_logger
from .models import BenchmarkMove, MarketSnapshot

logger = get_logger(__name__)

# Ventana por defecto del contexto de mercado: ~1 rueda US, igual que el default
# de los triggers (window_minutes 390). El caller puede pasar otra.
_DEFAULT_WINDOW_MINUTES = 390


class BenchmarkSource(Protocol):
    def enabled(self) -> list[Benchmark]: ...


class HoldingsSource(Protocol):
    def shares_by_ticker(self) -> dict[str, float]: ...


class PriceWindowReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...
    def reference_price(self, ticker: str, since: datetime) -> float | None: ...


class MarketContextService:
    """Calcula el movimiento de benchmarks + amplitud de las posiciones."""

    def __init__(
        self,
        benchmarks: BenchmarkSource,
        holdings: HoldingsSource,
        prices: PriceWindowReader,
        window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    ) -> None:
        self._benchmarks = benchmarks
        self._holdings = holdings
        self._prices = prices
        self._window_minutes = window_minutes

    @classmethod
    def from_engine(
        cls, engine: Engine, window_minutes: int = _DEFAULT_WINDOW_MINUTES
    ) -> MarketContextService:
        return cls(
            benchmarks=BenchmarksRepository(engine),
            holdings=HoldingsRepository(engine),
            prices=PriceRepository(engine),
            window_minutes=window_minutes,
        )

    def snapshot(self, now: datetime | None = None) -> MarketSnapshot | None:
        """Foto del mercado en la ventana (None si algo falla; siempre best-effort)."""
        try:
            return self._snapshot(now or datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001 - enriquecimiento opcional
            logger.warning("Contexto de mercado no disponible (%s); sigo sin él.", exc)
            return None

    def _snapshot(self, now: datetime) -> MarketSnapshot:
        since = now - timedelta(minutes=self._window_minutes)

        moves: list[BenchmarkMove] = []
        for b in self._benchmarks.enabled():
            pct = self._pct_change(b.ticker, since)
            if pct is not None:
                moves.append(BenchmarkMove(label=b.label, pct_change=pct))

        down = total = 0
        for ticker in self._holdings.shares_by_ticker():
            pct = self._pct_change(ticker, since)
            if pct is None:
                continue
            total += 1
            if pct < 0:
                down += 1

        return MarketSnapshot(
            benchmarks=tuple(moves),
            breadth_down=down,
            breadth_total=total,
            window_minutes=self._window_minutes,
        )

    def _pct_change(self, ticker: str, since: datetime) -> float | None:
        """% de cambio del ticker en la ventana [since, now] (None si falta dato)."""
        reference = self._prices.reference_price(ticker, since)
        current = self._prices.latest_price(ticker)
        if reference is None or current is None or reference == 0:
            return None
        return (current - reference) / reference * 100.0
