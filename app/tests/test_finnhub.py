"""Tests del cliente Finnhub usando httpx.MockTransport (sin red)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.finnhub import FinnhubClient, FinnhubError


def _settings() -> Settings:
    return Settings(_env_file=None, finnhub_api_key="test-key")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> FinnhubClient:
    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://finnhub.io/api/v1",
    )
    return FinnhubClient(_settings(), client=http)


def test_get_quote_parses_price_and_ts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "NVDA"
        assert request.url.params["token"] == "test-key"
        return httpx.Response(200, json={"c": 123.45, "t": 1700000000, "dp": 1.2})

    quote = _client(handler).get_quote("NVDA")

    assert quote.ticker == "NVDA"
    assert quote.price == 123.45
    assert quote.ts == datetime.fromtimestamp(1700000000, tz=UTC)


def test_get_quote_raises_on_empty_quote() -> None:
    # `c`==0 y `t`==0: símbolo desconocido en el free tier.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"c": 0, "t": 0})

    with pytest.raises(FinnhubError):
        _client(handler).get_quote("BOGUS")


def test_get_quote_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(FinnhubError):
        _client(handler).get_quote("NVDA")


def test_missing_api_key_raises() -> None:
    with pytest.raises(FinnhubError):
        FinnhubClient(Settings(_env_file=None, finnhub_api_key=""))
