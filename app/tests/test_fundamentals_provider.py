"""Tests del FMP fundamentals provider con httpx.MockTransport (sin red)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.edgar_fmp import FMPFundamentalsProvider, FundamentalsError


def _settings() -> Settings:
    return Settings(_env_file=None, fmp_api_key="test-key")


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> FMPFundamentalsProvider:
    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://financialmodelingprep.com/api/v3",
    )
    return FMPFundamentalsProvider(_settings(), client=http)


def test_fetch_normalizes_ratios_and_growth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["apikey"] == "test-key"
        if "ratios-ttm" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "peRatioTTM": 30.5,
                        "grossProfitMarginTTM": 0.75,
                        "debtEquityRatioTTM": 0.4,
                    }
                ],
            )
        if "financial-growth" in request.url.path:
            return httpx.Response(200, json=[{"revenueGrowth": 0.22}])
        return httpx.Response(404)

    fundamentals = _provider(handler).fetch("NVDA")

    assert fundamentals is not None
    assert fundamentals.ticker == "NVDA"
    assert fundamentals.pe == 30.5
    assert fundamentals.gross_margin == 0.75
    assert fundamentals.debt_to_equity == 0.4
    assert fundamentals.revenue_growth == 0.22
    assert fundamentals.source == "fmp"
    # el payload crudo se preserva
    assert fundamentals.raw["ratios_ttm"]["peRatioTTM"] == 30.5


def test_fetch_returns_none_when_no_ratios() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    assert _provider(handler).fetch("BOGUS") is None


def test_fetch_tolerates_missing_growth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "ratios-ttm" in request.url.path:
            return httpx.Response(200, json=[{"peRatioTTM": 10.0}])
        return httpx.Response(200, json=[])  # sin financial-growth

    fundamentals = _provider(handler).fetch("NVDA")

    assert fundamentals is not None
    assert fundamentals.pe == 10.0
    assert fundamentals.revenue_growth is None
    assert fundamentals.gross_margin is None  # campo ausente → None


def test_fetch_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(FundamentalsError):
        _provider(handler).fetch("NVDA")


def test_missing_api_key_raises() -> None:
    with pytest.raises(FundamentalsError):
        FMPFundamentalsProvider(Settings(_env_file=None, fmp_api_key=""))
