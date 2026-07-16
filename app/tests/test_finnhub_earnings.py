"""Tests del Finnhub earnings provider con httpx.MockTransport (sin red)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.finnhub import FinnhubEarningsProvider, FinnhubError


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> FinnhubEarningsProvider:
    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://finnhub.io/api/v1"
    )
    return FinnhubEarningsProvider(Settings(_env_file=None, finnhub_api_key="k"), client=http)


def test_fetch_parses_calendar() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "NVDA"
        assert "from" in request.url.params and "to" in request.url.params
        return httpx.Response(200, json={"earningsCalendar": [
            {"symbol": "NVDA", "date": "2026-08-27", "hour": "amc",
             "epsEstimate": 1.79, "epsActual": None,
             "revenueEstimate": 5.4e10, "revenueActual": None},
        ]})

    events = _provider(handler).fetch_upcoming("NVDA", 120)

    assert len(events) == 1
    e = events[0]
    assert e.ticker == "NVDA"
    assert e.earnings_date == date(2026, 8, 27)
    assert e.hour == "amc"
    assert e.eps_estimate == 1.79
    assert e.eps_actual is None


def test_fetch_skips_entries_without_date() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"earningsCalendar": [
            {"symbol": "NVDA", "date": None},
            {"symbol": "NVDA", "date": "2026-08-27", "hour": ""},
        ]})

    events = _provider(handler).fetch_upcoming("NVDA", 120)
    assert len(events) == 1
    assert events[0].hour == ""


def test_fetch_empty_calendar() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"earningsCalendar": []})

    assert _provider(handler).fetch_upcoming("BOGUS", 120) == []


def test_fetch_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    with pytest.raises(FinnhubError):
        _provider(handler).fetch_upcoming("NVDA", 120)
