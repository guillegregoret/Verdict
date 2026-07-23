"""Tests del PortfolioReviewService (reevaluación integral) con fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime

from portfolio_monitor.db.repositories import (
    AccountCashRow,
    FundamentalsRow,
    UpcomingEarnings,
)
from portfolio_monitor.reasoning import PortfolioReviewContext, Suggestion
from portfolio_monitor.review import PortfolioReviewService

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


class FakeHoldings:
    def __init__(self, shares: dict[str, float], verdicts: dict[str, str]) -> None:
        self._shares = shares
        self._verdicts = verdicts

    def shares_by_ticker(self) -> dict[str, float]:
        return dict(self._shares)

    def verdicts_by_ticker(self) -> dict[str, str]:
        return dict(self._verdicts)


class FakePrices:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def latest_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)


class FakeFundamentals:
    def __init__(self, rows: dict[str, FundamentalsRow]) -> None:
        self._rows = rows

    def latest(self, ticker: str) -> FundamentalsRow | None:
        return self._rows.get(ticker)


class FakeCash:
    def __init__(self, rows: list[AccountCashRow]) -> None:
        self._rows = rows

    def latest(self) -> list[AccountCashRow]:
        return list(self._rows)


class FakeEarnings:
    def __init__(self, rows: list[UpcomingEarnings]) -> None:
        self._rows = rows

    def upcoming(self, start: date, end: date) -> list[UpcomingEarnings]:
        return [r for r in self._rows if start <= r.earnings_date <= end]


class FakeReasoner:
    def __init__(self) -> None:
        self.context: PortfolioReviewContext | None = None

    def review(self, context: PortfolioReviewContext) -> Suggestion:
        self.context = context
        return Suggestion(text="REVIEW OK", source="template")


def _service(
    reasoner: FakeReasoner,
    shares: dict[str, float] | None = None,
    prices: dict[str, float] | None = None,
    earnings: list[UpcomingEarnings] | None = None,
) -> PortfolioReviewService:
    return PortfolioReviewService(
        holdings=FakeHoldings(
            shares if shares is not None else {"NVDA": 20.0, "GOOG": 5.0},
            {"NVDA": "Mantener", "GOOG": "Crecer"},
        ),
        prices=FakePrices(prices or {"NVDA": 200.0, "GOOG": 180.0}),
        fundamentals=FakeFundamentals(
            {"NVDA": FundamentalsRow("NVDA", NOW, 31.0, 0.70, 0.75, 0.30)}
        ),
        cash=FakeCash([AccountCashRow("U1", "Satélite IA", 69.0, 69.0, "USD")]),
        earnings=FakeEarnings(earnings or []),
        reasoner=reasoner,
    )


def test_review_returns_reasoner_text() -> None:
    assert _service(FakeReasoner()).review(now=NOW) == "REVIEW OK"


def test_context_has_weights_totals_and_sorting() -> None:
    r = FakeReasoner()
    _service(r).review(now=NOW)
    ctx = r.context
    assert ctx is not None
    # valor = 20*200 + 5*180 = 4000 + 900 = 4900
    assert ctx.total_value == 4900.0
    assert ctx.position_count == 2
    assert ctx.total_cash == 69.0
    # NVDA pesa 4000/4900 ≈ 81.6% → primero y marcado como concentración
    assert ctx.positions_block.splitlines()[0].startswith("• NVDA")
    assert "81.6%" in ctx.positions_block
    assert ctx.note is not None and "NVDA" in ctx.note


def test_missing_fundamentals_is_flagged_per_position() -> None:
    r = FakeReasoner()
    _service(r).review(now=NOW)
    # GOOG no tiene fundamentals en el fake
    goog_line = [ln for ln in r.context.positions_block.splitlines() if "GOOG" in ln][0]
    assert "no disponibles" in goog_line


def test_upcoming_earnings_annotated() -> None:
    r = FakeReasoner()
    _service(
        r, earnings=[UpcomingEarnings("NVDA", date(2026, 7, 28), "amc", 1.8, "Mantener")]
    ).review(now=NOW)
    nvda_line = [ln for ln in r.context.positions_block.splitlines() if "NVDA" in ln][0]
    assert "earnings 28/07" in nvda_line


def test_no_holdings_returns_message_without_calling_reasoner() -> None:
    r = FakeReasoner()
    out = _service(r, shares={}).review(now=NOW)
    assert "Sin holdings" in out
    assert r.context is None  # no se llamó al reasoner
