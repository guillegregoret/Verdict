"""Tests del Finnhub ratings provider con httpx.MockTransport (sin red)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.finnhub import FinnhubError, FinnhubRatingsProvider


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> FinnhubRatingsProvider:
    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://finnhub.io/api/v1"
    )
    return FinnhubRatingsProvider(Settings(_env_file=None, finnhub_api_key="k"), client=http)


def test_fetch_parses_recommendation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "NVDA"
        return httpx.Response(200, json=[
            {"symbol": "NVDA", "period": "2026-07-01",
             "strongBuy": 24, "buy": 40, "hold": 4, "sell": 0, "strongSell": 0},
        ])

    snaps = _provider(handler).fetch("NVDA")
    assert len(snaps) == 1
    s = snaps[0]
    assert s.ticker == "NVDA"
    assert s.period == date(2026, 7, 1)
    assert s.strong_buy == 24
    assert s.hold == 4


def test_fetch_skips_without_period() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"symbol": "NVDA", "period": None},
            {"symbol": "NVDA", "period": "2026-07-01", "strongBuy": 1},
        ])

    snaps = _provider(handler).fetch("NVDA")
    assert len(snaps) == 1
    assert snaps[0].strong_buy == 1
    assert snaps[0].buy == 0  # campos ausentes → 0


def test_fetch_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "no access"})

    with pytest.raises(FinnhubError):
        _provider(handler).fetch("NVDA")
