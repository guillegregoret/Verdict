"""Tests del PostEarningsMonitor (reacción post-earnings, §5) con fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import LastAlert, ReportedEarnings
from portfolio_monitor.earnings import PostEarningsMonitor

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _reported(
    ticker="NVDA", day="2026-07-16", est=1.80, actual=1.87, verdict="Mantener"
) -> ReportedEarnings:
    return ReportedEarnings(
        ticker=ticker, earnings_date=date.fromisoformat(day),
        eps_estimate=est, eps_actual=actual, verdict=verdict,
    )


class FakeEarnings:
    def __init__(self, rows: list[ReportedEarnings]) -> None:
        self._rows = rows

    def reported_since(self, start) -> list[ReportedEarnings]:
        return list(self._rows)


class FakePrices:
    def __init__(self, latest=None, before=None) -> None:
        self._latest = latest or {}
        self._before = before or {}

    def latest_price(self, ticker: str) -> float | None:
        return self._latest.get(ticker)

    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None:
        return self._before.get(ticker)


class FakeAlerts:
    def __init__(self, cooling: set[str] = frozenset()) -> None:
        self._cooling = cooling

    def last_alert(self, ticker, since, trigger_type=None) -> LastAlert | None:
        if ticker in self._cooling:
            return LastAlert(ts=NOW, pct_change=0.0, trigger_type="post_earnings")
        return None


def _monitor(rows, latest=None, before=None, cooling=frozenset()) -> PostEarningsMonitor:
    return PostEarningsMonitor(
        earnings=FakeEarnings(rows),
        prices=FakePrices(latest, before),
        alerts=FakeAlerts(cooling),
        settings=Settings(_env_file=None),
    )


def test_reports_surprise_and_reaction() -> None:
    m = _monitor(
        [_reported()],
        latest={"NVDA": 210.0},
        before={"NVDA": 200.0},  # +5% desde el reporte
    )
    events = m.evaluate(now=NOW)
    assert len(events) == 1
    note = events[0].note
    assert "sorpresa +4%" in note   # (1.87-1.80)/1.80 ≈ +3.9% → +4%
    assert "precio +5.0%" in note


def test_missing_estimate_omits_surprise() -> None:
    m = _monitor([_reported(est=None)], latest={"NVDA": 210.0}, before={"NVDA": 200.0})
    note = m.evaluate(now=NOW)[0].note
    assert "sorpresa" not in note
    assert "precio +5.0%" in note  # se apoya en la reacción de precio


def test_implausible_surprise_is_omitted() -> None:
    # quirk de listing extranjero (est 24.57 vs actual 138.87 → +465%): se descarta
    m = _monitor(
        [_reported(est=24.57, actual=138.87)],
        latest={"NVDA": 200.0}, before={"NVDA": 211.0},
    )
    note = m.evaluate(now=NOW)[0].note
    assert "sorpresa" not in note
    assert "precio -5.2%" in note


def test_not_held_is_skipped() -> None:
    assert _monitor([_reported(verdict=None)]).evaluate(now=NOW) == []


def test_cooldown_suppresses() -> None:
    m = _monitor([_reported()], cooling={"NVDA"})
    assert m.evaluate(now=NOW) == []


def test_signals_wraps_events() -> None:
    sigs = _monitor([_reported()], latest={"NVDA": 210.0}, before={"NVDA": 200.0}).signals()
    assert len(sigs) == 1
    assert sigs[0].trigger_type == "post_earnings"
    assert sigs[0].context.signal_kind == "post_earnings"
