"""Tests de integración de los repos contra Postgres real.

Se SALTAN automáticamente si no hay una DB alcanzable (ej: corriendo localmente
fuera de la red de compose). Verifican el schema/seed de las migraciones y el
roundtrip de escritura. Limpian sus propias filas con marcadores sintéticos.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, text

from portfolio_monitor.config import get_settings
from portfolio_monitor.data.ibkr import Position
from portfolio_monitor.db.repositories import (
    DEFAULT_VERDICT,
    AlertRepository,
    DataSourceHealthRepository,
    HoldingsRepository,
    PricePoint,
    PriceRepository,
    TickerConfigRepository,
)


def _fetch_holding(engine: Engine, ibkr_id: str, ticker: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT h.shares, h.avg_cost, h.verdict FROM holdings h "
                "JOIN accounts a ON a.id = h.account_id "
                "WHERE a.ibkr_id = :i AND h.ticker = :t"
            ),
            {"i": ibkr_id, "t": ticker},
        ).one_or_none()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    settings = get_settings()
    try:
        # create_engine importa el driver (psycopg) de forma eager: si falta,
        # también debe traducirse a skip, no a error.
        eng = create_engine(settings.sqlalchemy_url, future=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - driver ausente o DB inalcanzable → skip
        pytest.skip(f"Postgres no disponible: {exc}")
    yield eng
    eng.dispose()


def test_enabled_tickers_reflects_seed(engine: Engine) -> None:
    tickers = TickerConfigRepository(engine).enabled_tickers()
    assert "NVDA" in tickers              # US habilitado
    assert "RHM.DE" not in tickers        # europeo (fase 2) deshabilitado


def test_price_insert_roundtrip(engine: Engine) -> None:
    repo = PriceRepository(engine)
    marker = "__TEST_TICKER__"
    ts = datetime(2000, 1, 1, tzinfo=UTC)
    try:
        assert repo.insert_many([
            PricePoint(ticker=marker, ts=ts, price=1.23, source="test")
        ]) == 1
        # idempotencia: reinsertar el mismo (ticker, ts) no rompe
        repo.insert_many([PricePoint(ticker=marker, ts=ts, price=9.99, source="test")])
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT price FROM prices WHERE ticker = :t AND ts = :ts"),
                {"t": marker, "ts": ts},
            ).one()
        assert float(row.price) == 1.23   # ON CONFLICT DO NOTHING preservó el original
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM prices WHERE ticker = :t"), {"t": marker})


def test_health_record_roundtrip(engine: Engine) -> None:
    marker = "__test_source__"
    try:
        DataSourceHealthRepository(engine).record(marker, "up", latency_ms=42)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, latency_ms FROM data_source_health "
                    "WHERE source = :s ORDER BY ts DESC LIMIT 1"
                ),
                {"s": marker},
            ).one()
        assert row.status == "up"
        assert row.latency_ms == 42
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM data_source_health WHERE source = :s"), {"s": marker}
            )


def test_holdings_upsert_preserves_verdict(engine: Engine) -> None:
    repo = HoldingsRepository(engine)
    # NVDA está seedeado en U22106929 con verdict 'Mantener' y shares NULL.
    assert _fetch_holding(engine, "U22106929", "NVDA") is not None
    try:
        applied = repo.upsert_positions(
            [Position("U22106929", "NVDA", 12.0, 456.7, company="NVIDIA")]
        )
        assert applied == 1
        row = _fetch_holding(engine, "U22106929", "NVDA")
        assert float(row.shares) == 12.0
        assert float(row.avg_cost) == 456.7
        assert row.verdict == "Mantener"  # config preservada, NO DEFAULT_VERDICT
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE holdings SET shares = NULL, avg_cost = NULL "
                    "WHERE ticker = 'NVDA' AND account_id = "
                    "(SELECT id FROM accounts WHERE ibkr_id = 'U22106929')"
                )
            )


def test_holdings_upsert_new_ticker_gets_default_verdict(engine: Engine) -> None:
    repo = HoldingsRepository(engine)
    marker = "__ZZTEST__"
    try:
        assert repo.upsert_positions(
            [Position("U22106929", marker, 1.0, 2.0, company="Test Co")]
        ) == 1
        row = _fetch_holding(engine, "U22106929", marker)
        assert row.verdict == DEFAULT_VERDICT
        assert float(row.shares) == 1.0
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM holdings WHERE ticker = :t"), {"t": marker})


def test_holdings_upsert_unknown_account_is_ignored(engine: Engine) -> None:
    repo = HoldingsRepository(engine)
    assert repo.upsert_positions([Position("U00000000", "ZZZ", 1.0, 2.0)]) == 0


def test_enabled_configs_uses_seed_defaults(engine: Engine) -> None:
    by_ticker = {c.ticker: c for c in TickerConfigRepository(engine).enabled_configs()}
    assert "NVDA" in by_ticker
    assert by_ticker["NVDA"].threshold_pct == -4.5      # default de la migración
    assert by_ticker["NVDA"].window_minutes == 390


def test_verdicts_by_ticker_includes_seed(engine: Engine) -> None:
    verdicts = HoldingsRepository(engine).verdicts_by_ticker()
    assert verdicts.get("NVDA") == "Mantener"
    assert verdicts.get("GOOG") == "Crecer"


def test_price_latest_and_reference(engine: Engine) -> None:
    repo = PriceRepository(engine)
    marker = "__PZ__"
    t0 = datetime(2001, 1, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2001, 1, 1, 1, 0, tzinfo=UTC)
    try:
        repo.insert_many([
            PricePoint(marker, t0, 100.0, "test"),
            PricePoint(marker, t1, 90.0, "test"),
        ])
        assert repo.latest_price(marker) == 90.0
        assert repo.reference_price(marker, t0) == 100.0
        assert repo.latest_price("__NOPE__") is None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM prices WHERE ticker = :t"), {"t": marker})


def test_alert_record_and_cooldown_roundtrip(engine: Engine) -> None:
    repo = AlertRepository(engine)
    marker = "__ALRTZ__"
    since = datetime(2000, 1, 1, tzinfo=UTC)
    try:
        alert_id = repo.record(
            ticker=marker,
            trigger_type="drop_pct",
            pct_change=-5.0,
            window_minutes=390,
            verdict="Mantener",
            suggestion="test",
        )
        assert alert_id > 0
        assert marker in repo.alerted_tickers_since(since)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM alerts WHERE ticker = :t"), {"t": marker})
