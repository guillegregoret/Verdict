"""Tests del cliente IBKR con un IB fake (sin gateway ni ib_async instalado)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.ibkr import IBKRClient, IBKRError, Position


def _settings() -> Settings:
    return Settings(_env_file=None)


class FakeIB:
    """Sustituto de ib_async.IB para los tests."""

    def __init__(self, positions: list | None = None, connect_error: Exception | None = None):
        self._positions = positions or []
        self._connect_error = connect_error
        self.disconnected = False
        self.connect_kwargs: dict | None = None

    async def connectAsync(self, **kwargs: object) -> None:
        self.connect_kwargs = dict(kwargs)
        if self._connect_error:
            raise self._connect_error

    async def reqPositionsAsync(self) -> list:
        return self._positions

    def disconnect(self) -> None:
        self.disconnected = True


def _raw(account: str, symbol: str, shares: float, avg_cost: float, description=None):
    contract = SimpleNamespace(symbol=symbol, description=description)
    return SimpleNamespace(
        account=account, contract=contract, position=shares, avgCost=avg_cost
    )


def test_fetch_positions_maps_fields() -> None:
    raw = [_raw("U22106929", "NVDA", 10, 123.4, description="NVIDIA")]
    client = IBKRClient(_settings(), ib=FakeIB(positions=raw))

    positions = asyncio.run(client.fetch_positions())

    assert positions == [
        Position(
            account="U22106929",
            ticker="NVDA",
            shares=10.0,
            avg_cost=123.4,
            company="NVIDIA",
        )
    ]


def test_connect_is_readonly() -> None:
    fake = FakeIB()
    asyncio.run(IBKRClient(_settings(), ib=fake).connect())
    # 🔴 el cliente SIEMPRE se conecta en modo read-only
    assert fake.connect_kwargs["readonly"] is True


def test_connect_wraps_errors_as_ibkr_error() -> None:
    client = IBKRClient(_settings(), ib=FakeIB(connect_error=OSError("connection refused")))
    with pytest.raises(IBKRError):
        asyncio.run(client.connect())


def test_disconnect_delegates() -> None:
    fake = FakeIB()
    IBKRClient(_settings(), ib=fake).disconnect()
    assert fake.disconnected is True
