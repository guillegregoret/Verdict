"""Tests del HoldingsSyncService y del throttle de sync en el Scheduler.

Sin gateway, sin ib_async, sin DB: fuente de posiciones y sink inyectados.
"""

from __future__ import annotations

from portfolio_monitor.config import Settings
from portfolio_monitor.data.ibkr import IBKRError, Position
from portfolio_monitor.holdings import HoldingsSyncService
from portfolio_monitor.scheduler import Scheduler


def _settings(**over: object) -> Settings:
    return Settings(_env_file=None, **over)


class FakeSource:
    """Sustituto de IBKRClient: connect/fetch async, disconnect sync."""

    def __init__(
        self,
        positions: list[Position] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self._positions = positions or []
        self._connect_error = connect_error
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        if self._connect_error:
            raise self._connect_error
        self.connected = True

    async def fetch_positions(self) -> list[Position]:
        return self._positions

    def disconnect(self) -> None:
        self.disconnected = True


class FakeHoldings:
    def __init__(self) -> None:
        self.upserts: list[list[Position]] = []

    def upsert_positions(self, positions) -> int:
        self.upserts.append(list(positions))
        return len(positions)


def _pos(ticker: str = "NVDA") -> Position:
    return Position(account="U22106929", ticker=ticker, shares=10.0, avg_cost=100.0)


def test_run_once_fetches_and_upserts() -> None:
    source = FakeSource(positions=[_pos("NVDA"), _pos("GOOG")])
    holdings = FakeHoldings()
    svc = HoldingsSyncService(_settings(), source_factory=lambda: source, holdings=holdings)

    assert svc.run_once() == 2
    assert [p.ticker for p in holdings.upserts[0]] == ["NVDA", "GOOG"]
    assert source.connected and source.disconnected  # siempre desconecta


def test_run_once_gateway_down_returns_zero() -> None:
    source = FakeSource(connect_error=IBKRError("no gateway"))
    holdings = FakeHoldings()
    svc = HoldingsSyncService(_settings(), source_factory=lambda: source, holdings=holdings)

    assert svc.run_once() == 0        # best-effort: no propaga
    assert holdings.upserts == []     # no se toca la DB si no hubo fetch
    assert source.disconnected        # el finally desconecta igual


def test_run_once_empty_positions() -> None:
    source = FakeSource(positions=[])
    holdings = FakeHoldings()
    svc = HoldingsSyncService(_settings(), source_factory=lambda: source, holdings=holdings)

    assert svc.run_once() == 0
    assert holdings.upserts == [[]]


# ── Throttle en el Scheduler ─────────────────────────────────────────────────
class _CountingPoller:
    def __init__(self) -> None:
        self.calls = 0

    def poll_once(self) -> int:
        self.calls += 1
        return 0


class _CountingPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> int:
        self.calls += 1
        return 0


class _CountingSync:
    def __init__(self, error: bool = False) -> None:
        self.calls = 0
        self._error = error

    def run_once(self) -> int:
        self.calls += 1
        if self._error:
            raise RuntimeError("boom")
        return 0


def test_scheduler_syncs_holdings_every_n_ticks() -> None:
    sync = _CountingSync()
    sched = Scheduler(
        settings=_settings(holdings_sync_every_ticks=3),
        poller=_CountingPoller(),
        pipeline=_CountingPipeline(),
        holdings_sync=sync,
    )
    for _ in range(7):
        sched.tick()
    assert sync.calls == 3  # ticks 0, 3, 6


def test_scheduler_sync_disabled_when_zero() -> None:
    sync = _CountingSync()
    sched = Scheduler(
        settings=_settings(holdings_sync_every_ticks=0),
        poller=_CountingPoller(),
        pipeline=_CountingPipeline(),
        holdings_sync=sync,
    )
    for _ in range(5):
        sched.tick()
    assert sync.calls == 0


def test_scheduler_sync_failure_does_not_abort_tick() -> None:
    poller, pipeline = _CountingPoller(), _CountingPipeline()
    sched = Scheduler(
        settings=_settings(holdings_sync_every_ticks=1),
        poller=poller,
        pipeline=pipeline,
        holdings_sync=_CountingSync(error=True),
    )
    sched.tick()  # no debe propagar
    assert poller.calls == 1 and pipeline.calls == 1
