"""Price Poller: barre tickers habilitados → Finnhub → Postgres (§11.2).

Read-only: solo lee precios y los persiste. Un barrido = `poll_once()`. La
orquestación periódica final vivirá en el scheduler (§2); acá se ofrece también
un `run_forever()` simple para correrlo standalone.
"""

from __future__ import annotations

import time
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..data.finnhub import FinnhubError, Quote
from ..db.repositories import (
    DataSourceHealthRepository,
    PricePoint,
    PriceRepository,
    TickerConfigRepository,
)
from ..logging import get_logger

logger = get_logger(__name__)

_SOURCE = "finnhub"


# ── Protocolos: desacoplan al poller de las clases concretas (testabilidad) ──
class QuoteSource(Protocol):
    def get_quote(self, ticker: str) -> Quote: ...


class TickerConfigReader(Protocol):
    def enabled_tickers(self) -> list[str]: ...


class PriceSink(Protocol):
    def insert_many(self, points: list[PricePoint]) -> int: ...


class HealthSink(Protocol):
    def record(self, source: str, status: str, latency_ms: int | None = ...) -> None: ...


class PricePoller:
    """Orquesta un barrido de precios sobre los tickers habilitados."""

    def __init__(
        self,
        settings: Settings,
        quotes: QuoteSource,
        ticker_config: TickerConfigReader,
        prices: PriceSink,
        health: HealthSink,
    ) -> None:
        self._settings = settings
        self._quotes = quotes
        self._ticker_config = ticker_config
        self._prices = prices
        self._health = health

    @classmethod
    def from_engine(
        cls,
        settings: Settings,
        engine: Engine,
        quotes: QuoteSource,
    ) -> PricePoller:
        """Construye el poller cableando los repos concretos sobre `engine`."""
        return cls(
            settings=settings,
            quotes=quotes,
            ticker_config=TickerConfigRepository(engine),
            prices=PriceRepository(engine),
            health=DataSourceHealthRepository(engine),
        )

    def poll_once(self) -> int:
        """Un barrido completo. Devuelve la cantidad de puntos persistidos.

        Un ticker que falla no aborta el barrido: se loguea y se sigue.
        """
        tickers = self._ticker_config.enabled_tickers()
        if not tickers:
            logger.warning("No hay tickers habilitados en ticker_config.")
            return 0

        logger.info("Barrido de precios: %d tickers habilitados.", len(tickers))
        collected: list[PricePoint] = []
        failures = 0

        for i, ticker in enumerate(tickers):
            try:
                quote = self._quotes.get_quote(ticker)
            except FinnhubError as exc:
                failures += 1
                logger.warning("Quote %s falló: %s", ticker, exc)
            else:
                collected.append(
                    PricePoint(
                        ticker=quote.ticker,
                        ts=quote.ts,
                        price=quote.price,
                        source=_SOURCE,
                    )
                )
            # Rate-limit del free tier: espaciar salvo en el último.
            if i < len(tickers) - 1:
                time.sleep(self._settings.finnhub_request_spacing_seconds)

        inserted = self._prices.insert_many(collected)
        self._record_health(total=len(tickers), failures=failures)
        logger.info(
            "Barrido terminado: %d/%d ok, %d persistidos.",
            len(collected),
            len(tickers),
            inserted,
        )
        return inserted

    def run_forever(self) -> None:
        """Loop simple para correr el poller standalone (sin scheduler)."""
        interval = self._settings.poll_interval_seconds
        logger.info("Price poller arrancado (intervalo=%ss).", interval)
        while True:
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - un barrido no debe matar el proceso
                logger.exception("Barrido de precios falló por completo.")
            time.sleep(interval)

    def _record_health(self, total: int, failures: int) -> None:
        """Clasifica la salud de Finnhub según la tasa de fallos del barrido."""
        if failures == 0:
            status = "up"
        elif failures >= total:
            status = "down"
        else:
            status = "degraded"
        self._health.record(source=_SOURCE, status=status)
