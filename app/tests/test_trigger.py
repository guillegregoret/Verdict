"""Tests del TriggerEngine con fakes (sin DB)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from portfolio_monitor.db.repositories import LastAlert, TickerConfig
from portfolio_monitor.trigger import TriggerEngine

NOW = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


class FakeConfig:
    def __init__(self, configs: Iterable[TickerConfig]) -> None:
        self._configs = list(configs)

    def enabled_configs(self) -> list[TickerConfig]:
        return list(self._configs)


class FakePrices:
    def __init__(
        self,
        latest: dict[str, float],
        reference: dict[str, float],
        at: dict[str, float] | None = None,
        peak: dict[str, float] | None = None,
        trough: dict[str, float] | None = None,
    ) -> None:
        self._latest = latest
        self._reference = reference
        self._at = at or {}
        self._peak = peak or {}
        self._trough = trough or {}

    def latest_price(self, ticker: str) -> float | None:
        return self._latest.get(ticker)

    def reference_price(self, ticker: str, since: datetime) -> float | None:
        return self._reference.get(ticker)

    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None:
        return self._at.get(ticker)

    def max_price_since(self, ticker: str, since: datetime) -> float | None:
        return self._peak.get(ticker)

    def min_price_since(self, ticker: str, since: datetime) -> float | None:
        return self._trough.get(ticker)


class FakeVerdicts:
    def __init__(self, verdicts: dict[str, str]) -> None:
        self._verdicts = verdicts

    def verdicts_by_ticker(self) -> dict[str, str]:
        return dict(self._verdicts)


class FakeAlerts:
    def __init__(self, last: dict[str, LastAlert] | None = None) -> None:
        self._last = last or {}

    def last_alert(self, ticker: str, since: datetime) -> LastAlert | None:
        la = self._last.get(ticker)
        if la is None or la.ts < since:
            return None
        return la


def _cfg(
    ticker: str, threshold: float = -4.5, window: int = 390, rise: float = 8.0
) -> TickerConfig:
    return TickerConfig(
        ticker=ticker,
        threshold_pct=threshold,
        window_minutes=window,
        rise_threshold_pct=rise,
    )


def _engine(
    configs: list[TickerConfig],
    latest: dict[str, float],
    reference: dict[str, float],
    verdicts: dict[str, str],
    last_alerts: dict[str, LastAlert] | None = None,
    at: dict[str, float] | None = None,
    peak: dict[str, float] | None = None,
    trough: dict[str, float] | None = None,
) -> TriggerEngine:
    return TriggerEngine(
        configs=FakeConfig(configs),
        prices=FakePrices(latest, reference, at=at, peak=peak, trough=trough),
        verdicts=FakeVerdicts(verdicts),
        alerts=FakeAlerts(last_alerts),
    )


def test_drop_past_threshold_with_buy_verdict_emits_event() -> None:
    eng = _engine(
        [_cfg("NVDA")],
        latest={"NVDA": 95.0},
        reference={"NVDA": 100.0},
        verdicts={"NVDA": "Mantener"},
    )
    events = eng.evaluate(now=NOW)

    assert len(events) == 1
    ev = events[0]
    assert ev.ticker == "NVDA"
    assert ev.pct_change == pytest.approx(-5.0)
    assert ev.reference_price == 100.0
    assert ev.current_price == 95.0
    assert ev.verdict == "Mantener"
    assert ev.trigger_type == "drop_pct"


def test_drop_below_threshold_not_emitted() -> None:
    # cae 3% (< 4.5% de umbral) → no dispara
    eng = _engine([_cfg("NVDA")], {"NVDA": 97.0}, {"NVDA": 100.0}, {"NVDA": "Mantener"})
    assert eng.evaluate(now=NOW) == []


def test_non_buy_verdict_blocked_by_gate() -> None:
    eng = _engine([_cfg("MRVL")], {"MRVL": 90.0}, {"MRVL": 100.0}, {"MRVL": "Consolidar"})
    assert eng.evaluate(now=NOW) == []


def test_cooldown_suppresses_when_not_reverted() -> None:
    # alertó hace 1h por una caída y el precio NO recuperó → sigue en cooldown
    last = {"NVDA": LastAlert(NOW - timedelta(hours=1), pct_change=-6.0, trigger_type="drop_pct")}
    eng = _engine(
        [_cfg("NVDA")],
        latest={"NVDA": 90.0},
        reference={"NVDA": 100.0},
        verdicts={"NVDA": "Mantener"},
        last_alerts=last,
        at={"NVDA": 94.0},    # precio al alertar (referencia previa ≈ 100)
        peak={"NVDA": 95.0},  # no superó 97 (mitad del -6%) → no revirtió
    )
    assert eng.evaluate(now=NOW) == []


def test_cooldown_resets_after_recovery() -> None:
    # mismo caso pero el precio recuperó por encima del nivel → re-arma y vuelve a avisar
    last = {"NVDA": LastAlert(NOW - timedelta(hours=3), pct_change=-6.0, trigger_type="drop_pct")}
    eng = _engine(
        [_cfg("NVDA")],
        latest={"NVDA": 90.0},
        reference={"NVDA": 100.0},
        verdicts={"NVDA": "Mantener"},
        last_alerts=last,
        at={"NVDA": 94.0},    # referencia previa ≈ 100
        peak={"NVDA": 98.0},  # recuperó por encima de 97 → revirtió → cooldown liberado
    )
    assert len(eng.evaluate(now=NOW)) == 1


def test_cooldown_rise_resets_after_pullback() -> None:
    # alertó por una suba (+10%) y luego retrocedió lo suficiente → re-arma
    last = {"MU": LastAlert(NOW - timedelta(hours=2), pct_change=10.0, trigger_type="rise_pct")}
    eng = _engine(
        [_cfg("MU", rise=8.0)],
        latest={"MU": 112.0},          # +12% nuevo rally
        reference={"MU": 100.0},
        verdicts={"MU": "Trim - tomar ganancias"},
        last_alerts=last,
        at={"MU": 110.0},              # referencia previa ≈ 100
        trough={"MU": 104.0},          # retrocedió a ≤ 105 (mitad del +10%) → revirtió
    )
    assert len(eng.evaluate(now=NOW)) == 1


def test_cooldown_ignores_alert_outside_lookback() -> None:
    # una alerta de hace 2 días queda fuera de la ventana → no genera cooldown
    last = {"NVDA": LastAlert(NOW - timedelta(days=2), pct_change=-6.0, trigger_type="drop_pct")}
    eng = _engine(
        [_cfg("NVDA")],
        latest={"NVDA": 90.0},
        reference={"NVDA": 100.0},
        verdicts={"NVDA": "Mantener"},
        last_alerts=last,
    )
    assert len(eng.evaluate(now=NOW)) == 1


def test_insufficient_data_is_skipped() -> None:
    # sin precio de referencia en la ventana
    eng = _engine([_cfg("NVDA")], {"NVDA": 90.0}, {}, {"NVDA": "Mantener"})
    assert eng.evaluate(now=NOW) == []


def test_rise_does_not_trigger_buy_verdict() -> None:
    # una SUBA en un veredicto de compra (Mantener) no dispara nada
    eng = _engine([_cfg("NVDA")], {"NVDA": 110.0}, {"NVDA": 100.0}, {"NVDA": "Mantener"})
    assert eng.evaluate(now=NOW) == []


def test_rise_past_threshold_with_trim_verdict_emits_take_profit() -> None:
    # MU en "Trim - tomar ganancias" sube +6% (> umbral de suba 4.5) → aviso
    eng = _engine(
        [_cfg("MU", rise=4.5)],
        latest={"MU": 106.0},
        reference={"MU": 100.0},
        verdicts={"MU": "Trim - tomar ganancias"},
    )
    events = eng.evaluate(now=NOW)

    assert len(events) == 1
    ev = events[0]
    assert ev.ticker == "MU"
    assert ev.pct_change == pytest.approx(6.0)
    assert ev.trigger_type == "rise_pct"
    assert ev.action == "tomar_ganancias"


def test_rise_threshold_is_independent_from_drop_threshold() -> None:
    # +6% NO alcanza el umbral de suba por defecto (8.0), aunque supere |drop|
    eng = _engine(
        [_cfg("MU", threshold=-4.5, rise=8.0)],
        latest={"MU": 106.0},
        reference={"MU": 100.0},
        verdicts={"MU": "Trim - tomar ganancias"},
    )
    assert eng.evaluate(now=NOW) == []


def test_rise_with_consolidar_verdict_emits_consolidate() -> None:
    eng = _engine(
        [_cfg("QCOM")], {"QCOM": 110.0}, {"QCOM": 100.0}, {"QCOM": "Consolidar"}
    )
    events = eng.evaluate(now=NOW)

    assert len(events) == 1
    assert events[0].action == "consolidar"
    assert events[0].trigger_type == "rise_pct"


def test_rise_below_threshold_not_emitted() -> None:
    # sube 3% (< 4.5%) → no dispara
    eng = _engine([_cfg("MU")], {"MU": 103.0}, {"MU": 100.0}, {"MU": "Trim - tomar ganancias"})
    assert eng.evaluate(now=NOW) == []


def test_drop_on_trim_verdict_not_emitted() -> None:
    # un Trim que CAE no dispara (solo dispara al subir)
    eng = _engine([_cfg("MU")], {"MU": 90.0}, {"MU": 100.0}, {"MU": "Trim - tomar ganancias"})
    assert eng.evaluate(now=NOW) == []


def test_non_actionable_verdict_never_triggers() -> None:
    # "Mantener - no sumar" no dispara ni cayendo ni subiendo
    capped = "Mantener - no sumar"
    eng_drop = _engine([_cfg("AMD")], {"AMD": 80.0}, {"AMD": 100.0}, {"AMD": capped})
    eng_rise = _engine([_cfg("AMD")], {"AMD": 120.0}, {"AMD": 100.0}, {"AMD": capped})
    assert eng_drop.evaluate(now=NOW) == []
    assert eng_rise.evaluate(now=NOW) == []


def test_evaluates_multiple_tickers_independently() -> None:
    eng = _engine(
        [_cfg("NVDA"), _cfg("GOOG"), _cfg("MRVL")],
        latest={"NVDA": 95.0, "GOOG": 80.0, "MRVL": 50.0},
        reference={"NVDA": 100.0, "GOOG": 100.0, "MRVL": 100.0},
        verdicts={"NVDA": "Mantener", "GOOG": "Crecer", "MRVL": "Consolidar"},
    )
    tickers = {ev.ticker for ev in eng.evaluate(now=NOW)}
    # NVDA(-5%) y GOOG(-20%) pasan; MRVL cae -50% pero el gate lo bloquea
    assert tickers == {"NVDA", "GOOG"}
