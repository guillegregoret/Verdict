"""Tests del FundamentalsMonitor (deterioro de la tesis, §5.3) con fakes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import FundamentalsRow, LastAlert
from portfolio_monitor.fundamentals import FundamentalsMonitor

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _row(
    ts: datetime,
    revenue_growth: float | None = 0.70,
    gross_margin: float | None = 0.75,
    debt_to_equity: float | None = 0.30,
) -> FundamentalsRow:
    return FundamentalsRow(
        ticker="NVDA", ts=ts, pe=30.0,
        revenue_growth=revenue_growth,
        gross_margin=gross_margin,
        debt_to_equity=debt_to_equity,
    )


class FakeVerdicts:
    def __init__(self, verdicts: dict[str, str]) -> None:
        self._verdicts = verdicts

    def verdicts_by_ticker(self) -> dict[str, str]:
        return dict(self._verdicts)


class FakeHistory:
    def __init__(
        self, data: dict[str, tuple[FundamentalsRow, FundamentalsRow] | None]
    ) -> None:
        self._data = data

    def latest_and_baseline(self, ticker: str, min_gap_days: int):
        return self._data.get(ticker)


class FakeAlerts:
    def __init__(self, cooling: set[str] = frozenset()) -> None:
        self._cooling = cooling

    def last_alert(self, ticker, since, trigger_type=None) -> LastAlert | None:
        if ticker in self._cooling:
            return LastAlert(ts=NOW, pct_change=0.0, trigger_type="fundamentals_decay")
        return None


def _monitor(history, verdicts=None, cooling=frozenset()) -> FundamentalsMonitor:
    return FundamentalsMonitor(
        verdicts=FakeVerdicts(verdicts or {"NVDA": "Mantener"}),
        fundamentals=FakeHistory(history),
        alerts=FakeAlerts(cooling),
        settings=Settings(_env_file=None),
    )


def test_margin_compression_triggers() -> None:
    old = _row(NOW - timedelta(days=90), gross_margin=0.75)
    new = _row(NOW, gross_margin=0.68)  # -7pp ≥ 5pp
    events = _monitor({"NVDA": (new, old)}).evaluate(now=NOW)

    assert len(events) == 1
    assert events[0].ticker == "NVDA"
    assert events[0].trigger_type == "fundamentals_decay"
    assert any("margen bruto" in r for r in events[0].reasons)


def test_revenue_growth_drop_triggers() -> None:
    old = _row(NOW - timedelta(days=90), revenue_growth=0.70)
    new = _row(NOW, revenue_growth=0.50)  # -20pp ≥ 15pp
    events = _monitor({"NVDA": (new, old)}).evaluate(now=NOW)
    assert len(events) == 1
    assert any("crecimiento de ingresos" in r for r in events[0].reasons)


def test_debt_spike_triggers() -> None:
    old = _row(NOW - timedelta(days=90), debt_to_equity=0.30)
    new = _row(NOW, debt_to_equity=0.90)  # +0.60 ≥ 0.5
    events = _monitor({"NVDA": (new, old)}).evaluate(now=NOW)
    assert len(events) == 1
    assert any("deuda/equity" in r for r in events[0].reasons)


def test_healthy_does_not_trigger() -> None:
    old = _row(NOW - timedelta(days=90))
    new = _row(NOW)  # sin cambios
    assert _monitor({"NVDA": (new, old)}).evaluate(now=NOW) == []


def test_no_baseline_is_skipped() -> None:
    # sin historial suficiente (latest_and_baseline devuelve None)
    assert _monitor({"NVDA": None}).evaluate(now=NOW) == []


def test_cooldown_suppresses_repeat() -> None:
    old = _row(NOW - timedelta(days=90), gross_margin=0.75)
    new = _row(NOW, gross_margin=0.68)
    events = _monitor({"NVDA": (new, old)}, cooling={"NVDA"}).evaluate(now=NOW)
    assert events == []


def test_missing_metrics_are_ignored() -> None:
    old = _row(NOW - timedelta(days=90), gross_margin=None)
    new = _row(NOW, gross_margin=None)  # sin margen → no se puede comparar
    # revenue y deuda iguales → nada que reportar
    assert _monitor({"NVDA": (new, old)}).evaluate(now=NOW) == []
