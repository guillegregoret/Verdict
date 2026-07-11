"""Repositorios de acceso a datos (SQLAlchemy Core + SQL explícito).

Cubre el data layer (§11.2: tickers, precios, salud) y el sync del gateway
(§11.3: holdings). Otros dominios (alerts, fundamentals) sumarán sus repos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine, text

from ..logging import get_logger

logger = get_logger(__name__)

# Veredicto por defecto para posiciones descubiertas en IBKR que aún no están
# clasificadas. Conservador: NO sugiere compras (§4) hasta que el usuario lo edite.
DEFAULT_VERDICT = "Mantener - no sumar"


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


class PositionLike(Protocol):
    """Forma estructural de una posición (evita acoplar db → data.ibkr)."""

    account: str          # id de cuenta IBKR
    ticker: str
    shares: float
    avg_cost: float
    company: str | None


class HoldingsRepository:
    """Upsert de posiciones sincronizadas desde IBKR (§11.3).

    Preserva los campos de CONFIG editados por el usuario (verdict, thesis,
    cluster, target_pct): el sync solo actualiza shares/avg_cost/company. Las
    posiciones nuevas se insertan con `DEFAULT_VERDICT`.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_positions(
        self, positions: Sequence[PositionLike], default_verdict: str = DEFAULT_VERDICT
    ) -> int:
        """Sincroniza posiciones. Devuelve cuántas se aplicaron.

        Ignora posiciones de cuentas IBKR no registradas en `accounts` (loguea).
        """
        if not positions:
            return 0

        stmt = text(
            """
            INSERT INTO holdings
                (account_id, ticker, company, shares, avg_cost, verdict, updated_at)
            VALUES
                (:account_id, :ticker, :company, :shares, :avg_cost, :verdict, now())
            ON CONFLICT (account_id, ticker) DO UPDATE SET
                shares     = EXCLUDED.shares,
                avg_cost   = EXCLUDED.avg_cost,
                company    = COALESCE(EXCLUDED.company, holdings.company),
                updated_at = now()
            -- verdict/thesis/cluster/target_pct NO se tocan: son config del usuario.
            """
        )

        with self._engine.begin() as conn:
            id_by_ibkr = {
                row.ibkr_id: row.id
                for row in conn.execute(
                    text("SELECT id, ibkr_id FROM accounts WHERE ibkr_id IS NOT NULL")
                )
            }
            rows = []
            for p in positions:
                account_id = id_by_ibkr.get(p.account)
                if account_id is None:
                    logger.warning(
                        "Posición de cuenta IBKR desconocida %s (%s): se ignora.",
                        p.account,
                        p.ticker,
                    )
                    continue
                rows.append(
                    {
                        "account_id": account_id,
                        "ticker": p.ticker,
                        "company": p.company,
                        "shares": p.shares,
                        "avg_cost": p.avg_cost,
                        "verdict": default_verdict,
                    }
                )
            if rows:
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
