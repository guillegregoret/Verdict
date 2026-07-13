"""Tests del FundamentalsService con fakes (sin red ni DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portfolio_monitor.data.edgar_fmp import Fundamentals, FundamentalsError
from portfolio_monitor.db.repositories import FundamentalsRow
from portfolio_monitor.fundamentals import FundamentalsService

FIXED_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


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


def _row(ticker: str, ts: datetime) -> FundamentalsRow:
    return FundamentalsRow(
        ticker=ticker, ts=ts, pe=20.0, revenue_growth=0.1,
        gross_margin=0.5, debt_to_equity=0.3,
    )


class FakeProvider:
    def __init__(self, data: dict[str, Fundamentals], failing: set[str] = frozenset()):
        self._data = data
        self._failing = failing
        self.calls: list[str] = []

    def fetch(self, ticker: str) -> Fundamentals | None:
        self.calls.append(ticker)
        if ticker in self._failing:
            raise FundamentalsError(f"boom {ticker}")
        return self._data.get(ticker)


class FakeRepo:
    """Repo en memoria: insert_snapshot guarda y actualiza el 'latest'."""

    def __init__(self, initial: FundamentalsRow | None = None) -> None:
        self._latest = initial
        self.saved: list[Fundamentals] = []
        self.insert_ts = FIXED_NOW  # ts que la DB pondría al insertar

    def insert_snapshot(self, fundamentals: Fundamentals) -> None:
        self.saved.append(fundamentals)
        self._latest = FundamentalsRow(
            ticker=fundamentals.ticker, ts=self.insert_ts,
            pe=fundamentals.pe, revenue_growth=fundamentals.revenue_growth,
            gross_margin=fundamentals.gross_margin,
            debt_to_equity=fundamentals.debt_to_equity,
        )

    def latest(self, ticker: str) -> FundamentalsRow | None:
        return self._latest


# ── refresh() (batch) ────────────────────────────────────────────────────────
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


# ── latest() (on-trigger, con freshness) ─────────────────────────────────────
def test_latest_returns_cached_when_fresh() -> None:
    fresh = _row("NVDA", FIXED_NOW - timedelta(hours=1))
    provider = FakeProvider({"NVDA": _fundamentals("NVDA")})
    svc = FundamentalsService(provider, FakeRepo(initial=fresh), max_age_hours=24)

    assert svc.latest("NVDA", now=FIXED_NOW) is fresh
    assert provider.calls == []  # snapshot fresco: no se llama al provider


def test_latest_fetches_when_missing() -> None:
    provider = FakeProvider({"NVDA": _fundamentals("NVDA")})
    repo = FakeRepo(initial=None)
    svc = FundamentalsService(provider, repo, max_age_hours=24)

    row = svc.latest("NVDA", now=FIXED_NOW)
    assert row is not None and row.ticker == "NVDA"
    assert [f.ticker for f in repo.saved] == ["NVDA"]


def test_latest_refetches_when_stale() -> None:
    stale = _row("NVDA", FIXED_NOW - timedelta(hours=48))
    provider = FakeProvider({"NVDA": _fundamentals("NVDA")})
    repo = FakeRepo(initial=stale)
    svc = FundamentalsService(provider, repo, max_age_hours=24)

    svc.latest("NVDA", now=FIXED_NOW)
    assert provider.calls == ["NVDA"]
    assert [f.ticker for f in repo.saved] == ["NVDA"]


def test_latest_falls_back_to_previous_on_provider_error() -> None:
    stale = _row("NVDA", FIXED_NOW - timedelta(hours=48))
    provider = FakeProvider({}, failing={"NVDA"})
    repo = FakeRepo(initial=stale)
    svc = FundamentalsService(provider, repo, max_age_hours=24)

    assert svc.latest("NVDA", now=FIXED_NOW) is stale  # cae a lo previo
    assert repo.saved == []


def test_latest_returns_none_when_no_data() -> None:
    provider = FakeProvider({})  # devuelve None para NVDA
    repo = FakeRepo(initial=None)
    svc = FundamentalsService(provider, repo, max_age_hours=24)

    assert svc.latest("NVDA", now=FIXED_NOW) is None
    assert repo.saved == []
