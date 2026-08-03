"""Tests del formateo de /health (HealthReport → texto), sin DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portfolio_monitor.monitoring import HealthReport, format_health
from portfolio_monitor.monitoring.health import Component, _fmt_age, _fmt_uptime

NOW = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)


def _report(**over) -> HealthReport:
    base = dict(
        now=NOW,
        uptime_seconds=3 * 3600 + 25 * 60,
        db_ok=True,
        finnhub_status="up",
        last_price_ts=NOW - timedelta(minutes=1),
        last_holdings_sync=NOW - timedelta(minutes=10),
        last_cash_sync=NOW - timedelta(minutes=10),
        last_fundamentals_ts=NOW - timedelta(hours=5),
    )
    base.update(over)
    return HealthReport(**base)


def test_all_healthy_is_green() -> None:
    out = format_health(_report())
    assert "🩺 Estado de Verdict" in out
    assert "🟢 Base de datos" in out
    assert "🟢 Finnhub" in out
    assert "🟢 IBKR Gateway" in out
    assert "Uptime app: 3h 25m" in out
    assert "reconnect" not in out.lower()  # sin aviso cuando está fresco


def test_stale_ibkr_flags_yellow_and_suggests_reconnect() -> None:
    out = format_health(_report(last_holdings_sync=NOW - timedelta(hours=3)))
    assert "🟡 IBKR Gateway" in out
    assert "posiciones hace 3h" in out
    assert "/reconnect" in out


def test_missing_ibkr_sync_is_flagged() -> None:
    out = format_health(_report(last_holdings_sync=None, last_cash_sync=None))
    assert "🔴 IBKR Gateway" in out
    assert "sin datos" in out
    assert "/reconnect" in out


def test_db_down_is_red() -> None:
    out = format_health(_report(db_ok=False))
    assert "🔴 Base de datos" in out


def test_stale_price_flags_finnhub_yellow() -> None:
    out = format_health(_report(last_price_ts=NOW - timedelta(minutes=30)))
    assert "🟡 Finnhub" in out


def test_finnhub_down_status_is_red() -> None:
    out = format_health(_report(finnhub_status="down"))
    assert "🔴 Finnhub" in out


def test_container_components_are_rendered() -> None:
    out = format_health(
        _report(containers=(Component("ib-gateway", True, "up 2h"),))
    )
    assert "🟢 ib-gateway — up 2h" in out


def test_fmt_helpers() -> None:
    assert _fmt_age(None) == "sin datos"
    assert _fmt_age(30) == "hace 30s"
    assert _fmt_age(90) == "hace 1m"
    assert _fmt_age(3 * 3600 + 120) == "hace 3h 2m"
    assert _fmt_uptime(25 * 60) == "25m"
    assert _fmt_uptime(3 * 3600 + 25 * 60) == "3h 25m"
    assert _fmt_uptime(2 * 86400 + 3 * 3600) == "2d 3h 0m"
