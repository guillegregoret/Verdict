"""Repositorios de acceso a datos (SQLAlchemy Core + SQL explícito).

Cubre lo que necesita el data layer (§11.2): leer tickers habilitados y persistir
precios y salud de la fuente. Otros dominios (holdings, alerts, fundamentals)
sumarán sus repos en sus módulos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class PricePoint:
    """Un punto de precio a persistir en la hypertable `prices`."""

    ticker: str
    ts: datetime
    price: float
    source: str


class TickerConfigRepository:
    """Lectura de la config de triggers por ticker."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def enabled_tickers(self) -> list[str]:
        """Tickers con `enabled = true`, ordenados alfabéticamente."""
        stmt = text(
            "SELECT ticker FROM ticker_config WHERE enabled = true ORDER BY ticker"
        )
        with self._engine.connect() as conn:
            return [row.ticker for row in conn.execute(stmt)]


class PriceRepository:
    """Persistencia de la serie de precios."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_many(self, points: list[PricePoint]) -> int:
        """Inserta puntos de precio; ignora duplicados por (ticker, ts).

        Devuelve la cantidad de puntos enviados (no necesariamente insertados,
        por el ON CONFLICT DO NOTHING).
        """
        if not points:
            return 0
        stmt = text(
            """
            INSERT INTO prices (ticker, ts, price, source)
            VALUES (:ticker, :ts, :price, :source)
            ON CONFLICT (ticker, ts) DO NOTHING
            """
        )
        rows = [
            {"ticker": p.ticker, "ts": p.ts, "price": p.price, "source": p.source}
            for p in points
        ]
        with self._engine.begin() as conn:
            conn.execute(stmt, rows)
        return len(rows)


class DataSourceHealthRepository:
    """Registro de salud de las fuentes de datos (para el monitoreo, §9)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, source: str, status: str, latency_ms: int | None = None) -> None:
        """Anota un ping de salud de una fuente ('up' | 'down' | 'degraded')."""
        stmt = text(
            """
            INSERT INTO data_source_health (source, status, latency_ms)
            VALUES (:source, :status, :latency_ms)
            """
        )
        with self._engine.begin() as conn:
            conn.execute(
                stmt,
                {"source": source, "status": status, "latency_ms": latency_ms},
            )
