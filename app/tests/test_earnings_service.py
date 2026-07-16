"""Tests del EarningsService con fakes (sin red ni DB)."""

from __future__ import annotations

from datetime import date

from portfolio_monitor.data.finnhub import EarningsEvent, FinnhubError
from portfolio_monitor.earnings import EarningsService


def _event(ticker: str, day: str = "2026-08-15") -> EarningsEvent:
    return EarningsEvent(
        ticker=ticker, earnings_date=date.fromisoformat(day), hour="amc",
        eps_estimate=1.5, eps_actual=None,
        revenue_estimate=None, revenue_actual=None,
    )


class FakeProvider:
    def __init__(self, data: dict[str, list[EarningsEvent]], failing: set[str] = frozenset()):
        self._data = data
        self._failing = failing

    def fetch_upcoming(self, ticker: str, horizon_days: int) -> list[EarningsEvent]:
        if ticker in self._failing:
            raise FinnhubError(f"boom {ticker}")
        return self._data.get(ticker, [])


class FakeRepo:
    def __init__(self) -> None:
        self.saved: list[EarningsEvent] = []

    def upsert_many(self, events: list[EarningsEvent]) -> int:
        self.saved.extend(events)
        return len(events)


class FakeTickers:
    def __init__(self, tickers: list[str]) -> None:
        self._tickers = tickers

    def enabled_tickers(self) -> list[str]:
        return list(self._tickers)


def test_run_once_fetches_all_and_upserts() -> None:
    provider = FakeProvider({"NVDA": [_event("NVDA")], "GOOG": [_event("GOOG")]})
    repo = FakeRepo()
    svc = EarningsService(provider, repo, FakeTickers(["NVDA", "GOOG"]))

    assert svc.run_once() == 2
    assert {e.ticker for e in repo.saved} == {"NVDA", "GOOG"}


def test_run_once_ticker_failure_does_not_abort() -> None:
    provider = FakeProvider({"NVDA": [_event("NVDA")]}, failing={"GOOG"})
    repo = FakeRepo()
    svc = EarningsService(provider, repo, FakeTickers(["NVDA", "GOOG"]))

    # GOOG falla pero NVDA se guarda igual
    assert svc.run_once() == 1
    assert [e.ticker for e in repo.saved] == ["NVDA"]


def test_run_once_no_events() -> None:
    svc = EarningsService(FakeProvider({}), FakeRepo(), FakeTickers(["NVDA"]))
    assert svc.run_once() == 0
