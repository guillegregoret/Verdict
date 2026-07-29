"""Tests del MarketContextService (benchmarks + amplitud) con fakes."""

from __future__ import annotations

from datetime import UTC, datetime

from portfolio_monitor.db.repositories import Benchmark
from portfolio_monitor.market import MarketContextService, MarketSnapshot

NOW = datetime(2026, 7, 29, 20, 0, tzinfo=UTC)


class FakeBenchmarks:
    def __init__(self, rows: list[Benchmark]) -> None:
        self._rows = rows

    def enabled(self) -> list[Benchmark]:
        return list(self._rows)


class FakeHoldings:
    def __init__(self, shares: dict[str, float]) -> None:
        self._shares = shares

    def shares_by_ticker(self) -> dict[str, float]:
        return dict(self._shares)


class FakePrices:
    """Precio actual + referencia por ticker: (reference, latest)."""

    def __init__(self, series: dict[str, tuple[float, float]]) -> None:
        self._series = series

    def reference_price(self, ticker: str, since: datetime) -> float | None:
        v = self._series.get(ticker)
        return v[0] if v else None

    def latest_price(self, ticker: str) -> float | None:
        v = self._series.get(ticker)
        return v[1] if v else None


def _service(
    benchmarks: list[Benchmark],
    shares: dict[str, float],
    series: dict[str, tuple[float, float]],
) -> MarketContextService:
    return MarketContextService(
        benchmarks=FakeBenchmarks(benchmarks),
        holdings=FakeHoldings(shares),
        prices=FakePrices(series),
        window_minutes=390,
    )


def test_computes_benchmark_moves_and_breadth() -> None:
    svc = _service(
        benchmarks=[
            Benchmark("SPY", "S&P 500", "index"),
            Benchmark("SMH", "Semiconductors", "sector"),
        ],
        shares={"NVDA": 10.0, "AVGO": 5.0, "GOOG": 2.0},
        series={
            "SPY": (100.0, 98.0),   # -2.0%
            "SMH": (200.0, 194.0),  # -3.0%
            "NVDA": (100.0, 96.0),  # rojo
            "AVGO": (100.0, 97.0),  # rojo
            "GOOG": (100.0, 101.0),  # verde
        },
    )
    snap = svc.snapshot(NOW)
    assert snap is not None
    labels = {b.label: round(b.pct_change, 1) for b in snap.benchmarks}
    assert labels == {"S&P 500": -2.0, "Semiconductors": -3.0}
    assert snap.breadth_down == 2 and snap.breadth_total == 3
    assert "S&P 500 -2.0%" in snap.summary()
    assert "2/3 de mis posiciones en rojo" in snap.summary()


def test_skips_tickers_without_price_data() -> None:
    svc = _service(
        benchmarks=[Benchmark("SPY", "S&P 500", "index")],
        shares={"NVDA": 10.0, "NEW": 1.0},  # NEW sin serie → no cuenta
        series={"SPY": (100.0, 99.0), "NVDA": (100.0, 95.0)},
    )
    snap = svc.snapshot(NOW)
    assert snap is not None
    assert snap.breadth_total == 1  # solo NVDA tenía datos
    assert snap.breadth_down == 1


def test_snapshot_is_best_effort_on_error() -> None:
    class Boom:
        def enabled(self):  # noqa: ANN201
            raise RuntimeError("db down")

    svc = MarketContextService(
        benchmarks=Boom(),
        holdings=FakeHoldings({}),
        prices=FakePrices({}),
    )
    assert svc.snapshot(NOW) is None  # no propaga: enriquecimiento opcional


def test_empty_snapshot_has_no_data() -> None:
    snap = MarketSnapshot(benchmarks=(), breadth_down=0, breadth_total=0, window_minutes=390)
    assert snap.has_data is False
    assert snap.summary() == ""
