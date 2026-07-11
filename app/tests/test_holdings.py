"""Tests de orquestación del HoldingsSyncService con fakes (sin gateway ni DB)."""

from __future__ import annotations

import asyncio

from portfolio_monitor.data.ibkr import Position
from portfolio_monitor.holdings import HoldingsSyncService


class FakeSource:
    def __init__(self, positions: list[Position]) -> None:
        self._positions = positions

    async def fetch_positions(self) -> list[Position]:
        return list(self._positions)


class FakeHoldings:
    def __init__(self) -> None:
        self.received: list[Position] | None = None

    def upsert_positions(self, positions: list[Position]) -> int:
        self.received = list(positions)
        return len(positions)


def test_sync_once_upserts_all_positions() -> None:
    positions = [
        Position("U22106929", "NVDA", 10, 100.0),
        Position("U26716079", "LLY", 5, 800.0),
    ]
    sink = FakeHoldings()
    svc = HoldingsSyncService(source=FakeSource(positions), holdings=sink)

    assert asyncio.run(svc.sync_once()) == 2
    assert sink.received == positions


def test_sync_once_empty_does_not_touch_sink() -> None:
    sink = FakeHoldings()
    svc = HoldingsSyncService(source=FakeSource([]), holdings=sink)

    assert asyncio.run(svc.sync_once()) == 0
    assert sink.received is None  # no llamó al upsert
