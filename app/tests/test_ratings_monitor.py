"""Tests del RatingsMonitor (cambio de consenso de analistas, §5) con fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import LastAlert, RatingRow
from portfolio_monitor.ratings import RatingsMonitor

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _row(sb: int, b: int, h: int, s: int, ss: int, day: str = "2026-07-01") -> RatingRow:
    return RatingRow(
        ticker="NVDA", period=date.fromisoformat(day),
        strong_buy=sb, buy=b, hold=h, sell=s, strong_sell=ss,
    )


class FakeVerdicts:
    def __init__(self, v: dict[str, str]) -> None:
        self._v = v

    def verdicts_by_ticker(self) -> dict[str, str]:
        return dict(self._v)


class FakeHistory:
    def __init__(self, data) -> None:
        self._data = data

    def latest_and_baseline(self, ticker, min_gap_days):
        return self._data.get(ticker)


class FakeAlerts:
    def __init__(self, cooling: set[str] = frozenset()) -> None:
        self._cooling = cooling

    def last_alert(self, ticker, since, trigger_type=None) -> LastAlert | None:
        if ticker in self._cooling:
            return LastAlert(ts=NOW, pct_change=0.0, trigger_type="rating_shift")
        return None


def _monitor(history, verdicts=None, cooling=frozenset()) -> RatingsMonitor:
    return RatingsMonitor(
        verdicts=FakeVerdicts(verdicts or {"NVDA": "Mantener"}),
        ratings=FakeHistory(history),
        alerts=FakeAlerts(cooling),
        settings=Settings(_env_file=None),
    )


def test_deterioration_triggers() -> None:
    base = _row(24, 40, 4, 0, 0)   # score ~4.29
    cur = _row(10, 30, 28, 0, 0)   # score ~3.74 → -0.55
    events = _monitor({"NVDA": (cur, base)}).evaluate(now=NOW)
    assert len(events) == 1
    assert "se deterioró" in events[0].note


def test_improvement_triggers() -> None:
    base = _row(10, 30, 28, 0, 0)  # ~3.74
    cur = _row(24, 40, 4, 0, 0)    # ~4.29 → +0.55
    events = _monitor({"NVDA": (cur, base)}).evaluate(now=NOW)
    assert len(events) == 1
    assert "mejoró" in events[0].note


def test_small_change_does_not_trigger() -> None:
    base = _row(24, 40, 4, 0, 0)
    cur = _row(23, 41, 4, 0, 0)    # cambio mínimo (< 0.3)
    assert _monitor({"NVDA": (cur, base)}).evaluate(now=NOW) == []


def test_no_baseline_is_skipped() -> None:
    assert _monitor({"NVDA": None}).evaluate(now=NOW) == []


def test_cooldown_suppresses() -> None:
    base = _row(24, 40, 4, 0, 0)
    cur = _row(10, 30, 28, 0, 0)
    assert _monitor({"NVDA": (cur, base)}, cooling={"NVDA"}).evaluate(now=NOW) == []


def test_signals_wraps_events() -> None:
    base = _row(24, 40, 4, 0, 0)
    cur = _row(10, 30, 28, 0, 0)
    signals = _monitor({"NVDA": (cur, base)}).signals()
    assert len(signals) == 1
    assert signals[0].trigger_type == "rating_shift"
    assert signals[0].context.signal_kind == "rating_shift"
