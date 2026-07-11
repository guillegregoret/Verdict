"""Tests de orquestación del PricePoller con fakes (sin red ni DB)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from portfolio_monitor.config import Settings
from portfolio_monitor.data.finnhub import FinnhubError, Quote
from portfolio_monitor.db.repositories import PricePoint
from portfolio_monitor.poller import PricePoller


class FakeTickerConfig:
    def __init__(self, tickers: Iterable[str]) -> None:
        self._tickers = list(tickers)

    def enabled_tickers(self) -> list[str]:
        return list(self._tickers)


class FakePrices:
    def __init__(self) -> None:
        self.inserted: list[PricePoint] = []

    def insert_many(self, points: list[PricePoint]) -> int:
        self.inserted.extend(points)
        return len(points)


class FakeHealth:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def record(self, source: str, status: str, latency_ms: int | None = None) -> None:
        self.records.append((source, status))


class FakeQuotes:
    def __init__(self, prices: dict[str, float], failing: Iterable[str] = ()) -> None:
        self._prices = prices
        self._failing = set(failing)
        self.calls: list[str] = []

    def get_quote(self, ticker: str) -> Quote:
        self.calls.append(ticker)
        if ticker in self._failing:
            raise FinnhubError(f"boom {ticker}")
        return Quote(
            ticker=ticker,
            price=self._prices[ticker],
            ts=datetime(2026, 1, 1, tzinfo=UTC),
        )


def _settings() -> Settings:
    # spacing 0 → sin esperas reales entre requests durante el test.
    return Settings(_env_file=None, finnhub_request_spacing_seconds=0)


def _poller(
    quotes: FakeQuotes, tickers: list[str]
) -> tuple[PricePoller, FakePrices, FakeHealth]:
    prices, health = FakePrices(), FakeHealth()
    poller = PricePoller(
        settings=_settings(),
        quotes=quotes,
        ticker_config=FakeTickerConfig(tickers),
        prices=prices,
        health=health,
    )
    return poller, prices, health


def test_poll_once_all_success() -> None:
    quotes = FakeQuotes({"NVDA": 100.0, "GOOG": 200.0})
    poller, prices, health = _poller(quotes, ["NVDA", "GOOG"])

    assert poller.poll_once() == 2
    assert {p.ticker for p in prices.inserted} == {"NVDA", "GOOG"}
    assert all(p.source == "finnhub" for p in prices.inserted)
    assert health.records == [("finnhub", "up")]


def test_poll_once_partial_failure_does_not_abort() -> None:
    quotes = FakeQuotes({"NVDA": 100.0, "GOOG": 200.0}, failing=["NVDA"])
    poller, prices, health = _poller(quotes, ["NVDA", "GOOG"])

    assert poller.poll_once() == 1
    assert [p.ticker for p in prices.inserted] == ["GOOG"]
    assert health.records == [("finnhub", "degraded")]
    # el fallo del primero no impidió consultar el segundo
    assert quotes.calls == ["NVDA", "GOOG"]


def test_poll_once_all_failures_marks_down() -> None:
    quotes = FakeQuotes({"NVDA": 100.0}, failing=["NVDA"])
    poller, prices, health = _poller(quotes, ["NVDA"])

    assert poller.poll_once() == 0
    assert prices.inserted == []
    assert health.records == [("finnhub", "down")]


def test_poll_once_no_enabled_tickers_is_noop() -> None:
    poller, prices, health = _poller(FakeQuotes({}), [])

    assert poller.poll_once() == 0
    assert prices.inserted == []
    assert health.records == []  # early return: no se registra salud
