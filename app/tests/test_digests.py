"""Tests de los digests semanales con fakes (sin DB ni red)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import UpcomingEarnings
from portfolio_monitor.digests import (
    EarningsDigest,
    PortfolioDigest,
    WeeklyDigestRunner,
)

ET = ZoneInfo("America/New_York")
MON = datetime(2026, 7, 20, 8, 30, tzinfo=ET)   # lunes 08:30 ET
MON_EARLY = datetime(2026, 7, 20, 7, 0, tzinfo=ET)
FRI = datetime(2026, 7, 24, 16, 30, tzinfo=ET)  # viernes 16:30 ET
WED = datetime(2026, 7, 22, 10, 0, tzinfo=ET)


# ── EarningsDigest ───────────────────────────────────────────────────────────
class FakeEarnings:
    def __init__(self, rows: list[UpcomingEarnings]) -> None:
        self._rows = rows

    def upcoming(self, start: date, end: date) -> list[UpcomingEarnings]:
        return list(self._rows)


def test_earnings_digest_lists_events() -> None:
    rows = [UpcomingEarnings("NVDA", date(2026, 7, 22), "amc", 1.79, "Mantener")]
    text = EarningsDigest(FakeEarnings(rows)).build(MON)
    assert "NVDA" in text
    assert "Mantener" in text
    assert "post-cierre" in text


def test_earnings_digest_empty_week() -> None:
    text = EarningsDigest(FakeEarnings([])).build(MON)
    assert "ninguno" in text


# ── PortfolioDigest ──────────────────────────────────────────────────────────
class FakeShares:
    def __init__(self, shares: dict[str, float]) -> None:
        self._shares = shares

    def shares_by_ticker(self) -> dict[str, float]:
        return dict(self._shares)


class FakePrices:
    def __init__(self, latest: dict[str, float], prev: dict[str, float]) -> None:
        self._latest = latest
        self._prev = prev

    def latest_price(self, ticker: str) -> float | None:
        return self._latest.get(ticker)

    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None:
        return self._prev.get(ticker)


def test_portfolio_digest_computes_variation() -> None:
    holdings = FakeShares({"NVDA": 10.0, "GOOG": 5.0})
    prices = FakePrices(
        latest={"NVDA": 110.0, "GOOG": 200.0},
        prev={"NVDA": 100.0, "GOOG": 210.0},
    )
    text = PortfolioDigest(holdings, prices).build(FRI)
    assert text is not None
    assert "Resumen semanal" in text
    # NVDA +10%, GOOG -4.76%; total now 2100 vs prev 2050 → +2.44%
    assert "NVDA +10.0%" in text
    assert "GOOG -4.8%" in text


def test_portfolio_digest_no_holdings_is_none() -> None:
    assert PortfolioDigest(FakeShares({}), FakePrices({}, {})).build(FRI) is None


# ── WeeklyDigestRunner (time-gate + dedupe) ──────────────────────────────────
class FakeDigest:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def build(self, now: datetime) -> str | None:
        return self._text


class FakeLog:
    def __init__(self) -> None:
        self.sent: set[tuple[str, date]] = set()

    def was_sent(self, kind: str, day: date) -> bool:
        return (kind, day) in self.sent

    def mark_sent(self, kind: str, day: date) -> None:
        self.sent.add((kind, day))


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


def _runner(notifier: FakeNotifier, log: FakeLog | None = None) -> WeeklyDigestRunner:
    return WeeklyDigestRunner(
        earnings_digest=FakeDigest("EARN"),
        portfolio_digest=FakeDigest("PORT"),
        notifier=notifier,
        log=log or FakeLog(),
        settings=Settings(_env_file=None),
    )


def test_monday_sends_earnings() -> None:
    n = FakeNotifier()
    _runner(n).run(now=MON)
    assert n.sent == ["EARN"]


def test_friday_sends_portfolio() -> None:
    n = FakeNotifier()
    _runner(n).run(now=FRI)
    assert n.sent == ["PORT"]


def test_dedupes_same_day() -> None:
    n = FakeNotifier()
    runner = _runner(n)
    runner.run(now=MON)
    runner.run(now=MON)
    assert n.sent == ["EARN"]  # una sola vez


def test_before_target_hour_does_not_send() -> None:
    n = FakeNotifier()
    _runner(n).run(now=MON_EARLY)
    assert n.sent == []


def test_other_weekday_sends_nothing() -> None:
    n = FakeNotifier()
    _runner(n).run(now=WED)
    assert n.sent == []
