"""Tests del FundamentalsService con fakes (sin red ni DB)."""

from __future__ import annotations

from portfolio_monitor.data.edgar_fmp import Fundamentals, FundamentalsError
from portfolio_monitor.fundamentals import FundamentalsService


def _fundamentals(ticker: str) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        pe=20.0,
        revenue_growth=0.1,
        gross_margin=0.5,
        debt_to_equity=0.3,
        raw={"x": 1},
        source="fmp",
    )


class FakeProvider:
    def __init__(self, data: dict[str, Fundamentals], failing: set[str] = frozenset()):
        self._data = data
        self._failing = failing

    def fetch(self, ticker: str) -> Fundamentals | None:
        if ticker in self._failing:
            raise FundamentalsError(f"boom {ticker}")
        return self._data.get(ticker)


class FakeRepo:
    def __init__(self) -> None:
        self.saved: list[Fundamentals] = []

    def insert_snapshot(self, fundamentals: Fundamentals) -> None:
        self.saved.append(fundamentals)


def test_refresh_saves_available_fundamentals() -> None:
    provider = FakeProvider({"NVDA": _fundamentals("NVDA"), "GOOG": _fundamentals("GOOG")})
    repo = FakeRepo()
    svc = FundamentalsService(provider=provider, repo=repo)

    assert svc.refresh(["NVDA", "GOOG"]) == 2
    assert {f.ticker for f in repo.saved} == {"NVDA", "GOOG"}


def test_refresh_skips_missing_and_failing() -> None:
    provider = FakeProvider({"NVDA": _fundamentals("NVDA")}, failing={"GOOG"})
    repo = FakeRepo()
    svc = FundamentalsService(provider=provider, repo=repo)

    # NVDA ok, GOOG falla, MSFT sin datos (None) → solo se guarda NVDA
    assert svc.refresh(["NVDA", "GOOG", "MSFT"]) == 1
    assert [f.ticker for f in repo.saved] == ["NVDA"]
