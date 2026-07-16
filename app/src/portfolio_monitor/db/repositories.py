"""Repositorios de acceso a datos (SQLAlchemy Core + SQL explícito).

Cubre el data layer (§11.2: tickers, precios, salud) y el sync del gateway
(§11.3: holdings). Otros dominios (alerts, fundamentals) sumarán sus repos.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

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


@dataclass(frozen=True)
class TickerConfig:
    """Config de trigger de un ticker (umbrales + ventana)."""

    ticker: str
    threshold_pct: float            # umbral de CAÍDA (negativo, ej: -2.7)
    window_minutes: int
    rise_threshold_pct: float = 4.0  # umbral de SUBA (positivo, ej: +4.0)


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

    def enabled_configs(self) -> list[TickerConfig]:
        """Config completa (umbrales/ventana) de los tickers habilitados."""
        stmt = text(
            "SELECT ticker, threshold_pct, window_minutes, rise_threshold_pct "
            "FROM ticker_config WHERE enabled = true ORDER BY ticker"
        )
        with self._engine.connect() as conn:
            return [
                TickerConfig(
                    ticker=r.ticker,
                    threshold_pct=float(r.threshold_pct),
                    window_minutes=int(r.window_minutes),
                    rise_threshold_pct=float(r.rise_threshold_pct),
                )
                for r in conn.execute(stmt)
            ]


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

    def latest_price(self, ticker: str) -> float | None:
        """Último precio conocido de un ticker (o None si no hay datos)."""
        stmt = text(
            "SELECT price FROM prices WHERE ticker = :t ORDER BY ts DESC LIMIT 1"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker}).one_or_none()
        return float(row.price) if row else None

    def reference_price(self, ticker: str, since: datetime) -> float | None:
        """Primer precio en la ventana [since, now] — referencia del % de cambio."""
        stmt = text(
            "SELECT price FROM prices WHERE ticker = :t AND ts >= :since "
            "ORDER BY ts ASC LIMIT 1"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker, "since": since}).one_or_none()
        return float(row.price) if row else None

    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None:
        """Precio vigente en `ts` (el último ≤ ts) — precio al momento de una alerta."""
        stmt = text(
            "SELECT price FROM prices WHERE ticker = :t AND ts <= :ts "
            "ORDER BY ts DESC LIMIT 1"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker, "ts": ts}).one_or_none()
        return float(row.price) if row else None

    def max_price_since(self, ticker: str, since: datetime) -> float | None:
        """Precio máximo estrictamente después de `since` (recuperación tras caída)."""
        stmt = text(
            "SELECT max(price) AS p FROM prices WHERE ticker = :t AND ts > :since"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker, "since": since}).one_or_none()
        return float(row.p) if row and row.p is not None else None

    def min_price_since(self, ticker: str, since: datetime) -> float | None:
        """Precio mínimo estrictamente después de `since` (retroceso tras suba)."""
        stmt = text(
            "SELECT min(price) AS p FROM prices WHERE ticker = :t AND ts > :since"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker, "since": since}).one_or_none()
        return float(row.p) if row and row.p is not None else None


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

    def verdicts_by_ticker(self) -> dict[str, str]:
        """Mapa {ticker: verdict} para el Verdict Gate (§4)."""
        with self._engine.connect() as conn:
            return {
                r.ticker: r.verdict
                for r in conn.execute(text("SELECT ticker, verdict FROM holdings"))
            }


@dataclass(frozen=True)
class LastAlert:
    """Última alerta de un ticker — insumo del cooldown con reset por reversión."""

    ts: datetime
    pct_change: float
    trigger_type: str        # "drop_pct" | "rise_pct"


class AlertRepository:
    """Auditoría de alertas emitidas + soporte de cooldown (§5.5)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def alerted_tickers_since(self, since: datetime) -> set[str]:
        """Tickers con alguna alerta desde `since` (para el cooldown)."""
        stmt = text("SELECT DISTINCT ticker FROM alerts WHERE ts >= :since")
        with self._engine.connect() as conn:
            return {r.ticker for r in conn.execute(stmt, {"since": since})}

    def last_alert(
        self, ticker: str, since: datetime, trigger_type: str | None = None
    ) -> LastAlert | None:
        """Última alerta del ticker desde `since` (None si no hubo en la ventana).

        Si se pasa `trigger_type`, filtra por ese tipo (para cooldowns separados,
        ej: el deterioro de fundamentals no interfiere con las alertas de precio).
        """
        where = "ticker = :t AND ts >= :since"
        params: dict[str, object] = {"t": ticker, "since": since}
        if trigger_type is not None:
            where += " AND trigger_type = :tt"
            params["tt"] = trigger_type
        stmt = text(
            f"SELECT ts, pct_change, trigger_type FROM alerts "  # noqa: S608 (where fijo)
            f"WHERE {where} ORDER BY ts DESC LIMIT 1"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, params).one_or_none()
        if row is None:
            return None
        return LastAlert(
            ts=row.ts,
            pct_change=float(row.pct_change) if row.pct_change is not None else 0.0,
            trigger_type=row.trigger_type,
        )

    def record(
        self,
        ticker: str,
        trigger_type: str,
        pct_change: float,
        window_minutes: int,
        verdict: str,
        suggestion: str | None = None,
        bucket_remaining: float | None = None,
    ) -> int:
        """Persiste una alerta emitida. Devuelve el id generado."""
        stmt = text(
            """
            INSERT INTO alerts
                (ticker, trigger_type, pct_change, window_minutes, verdict,
                 suggestion, bucket_remaining)
            VALUES
                (:ticker, :trigger_type, :pct_change, :window_minutes, :verdict,
                 :suggestion, :bucket_remaining)
            RETURNING id
            """
        )
        with self._engine.begin() as conn:
            return conn.execute(
                stmt,
                {
                    "ticker": ticker,
                    "trigger_type": trigger_type,
                    "pct_change": pct_change,
                    "window_minutes": window_minutes,
                    "verdict": verdict,
                    "suggestion": suggestion,
                    "bucket_remaining": bucket_remaining,
                },
            ).scalar_one()


class FundamentalsLike(Protocol):
    """Forma estructural de un snapshot de fundamentals (evita acoplar db → data)."""

    ticker: str
    pe: float | None
    revenue_growth: float | None
    gross_margin: float | None
    debt_to_equity: float | None
    raw: dict[str, Any]
    source: str


@dataclass(frozen=True)
class FundamentalsRow:
    """Snapshot leído de la tabla `fundamentals` (sin el raw)."""

    ticker: str
    ts: datetime
    pe: float | None
    revenue_growth: float | None
    gross_margin: float | None
    debt_to_equity: float | None


class FundamentalsRepository:
    """Persistencia y lectura de snapshots de fundamentals (§5.3, §11.5)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_snapshot(
        self, fundamentals: FundamentalsLike, ts: datetime | None = None
    ) -> None:
        """Guarda un snapshot; ignora duplicados por (ticker, ts)."""
        stmt = text(
            """
            INSERT INTO fundamentals
                (ticker, ts, pe, revenue_growth, gross_margin, debt_to_equity,
                 raw, source)
            VALUES
                (:ticker, :ts, :pe, :revenue_growth, :gross_margin, :debt_to_equity,
                 CAST(:raw AS jsonb), :source)
            ON CONFLICT (ticker, ts) DO NOTHING
            """
        )
        with self._engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "ticker": fundamentals.ticker,
                    "ts": ts or datetime.now(UTC),
                    "pe": fundamentals.pe,
                    "revenue_growth": fundamentals.revenue_growth,
                    "gross_margin": fundamentals.gross_margin,
                    "debt_to_equity": fundamentals.debt_to_equity,
                    "raw": json.dumps(fundamentals.raw),
                    "source": fundamentals.source,
                },
            )

    @staticmethod
    def _row_from(row: Any) -> FundamentalsRow:
        def _f(v: Any) -> float | None:
            return float(v) if v is not None else None

        return FundamentalsRow(
            ticker=row.ticker,
            ts=row.ts,
            pe=_f(row.pe),
            revenue_growth=_f(row.revenue_growth),
            gross_margin=_f(row.gross_margin),
            debt_to_equity=_f(row.debt_to_equity),
        )

    def latest(self, ticker: str) -> FundamentalsRow | None:
        """Último snapshot de fundamentals de un ticker (para el §5.3)."""
        stmt = text(
            """
            SELECT ticker, ts, pe, revenue_growth, gross_margin, debt_to_equity
            FROM fundamentals WHERE ticker = :t ORDER BY ts DESC LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker}).one_or_none()
        return self._row_from(row) if row is not None else None

    def latest_and_baseline(
        self, ticker: str, min_gap_days: int
    ) -> tuple[FundamentalsRow, FundamentalsRow] | None:
        """(último, baseline) donde baseline es el snapshot más reciente que sea
        al menos `min_gap_days` más viejo que el último. None si falta alguno
        (no hay historial suficiente para comparar el cambio trimestral)."""
        latest = self.latest(ticker)
        if latest is None:
            return None
        cutoff = latest.ts - timedelta(days=min_gap_days)
        stmt = text(
            """
            SELECT ticker, ts, pe, revenue_growth, gross_margin, debt_to_equity
            FROM fundamentals WHERE ticker = :t AND ts <= :cutoff
            ORDER BY ts DESC LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"t": ticker, "cutoff": cutoff}).one_or_none()
        if row is None:
            return None
        return latest, self._row_from(row)


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
