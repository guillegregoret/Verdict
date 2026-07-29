"""Tests del Finnhub fundamentals provider con httpx.MockTransport (sin red)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.edgar_fmp import FundamentalsError
from portfolio_monitor.data.finnhub import FinnhubFundamentalsProvider


def _settings() -> Settings:
    return Settings(_env_file=None, finnhub_api_key="test-key")


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> FinnhubFundamentalsProvider:
    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://finnhub.io/api/v1",
    )
    return FinnhubFundamentalsProvider(_settings(), client=http)


def test_fetch_normalizes_percentages_to_fractions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "NVDA"
        assert request.url.params["metric"] == "all"
        return httpx.Response(200, json={"metric": {
            "peTTM": 31.5,
            "revenueGrowthTTMYoy": 70.68,   # % → 0.7068
            "grossMarginTTM": 74.15,        # % → 0.7415
            "totalDebt/totalEquityQuarterly": 0.0433,
        }})

    f = _provider(handler).fetch("NVDA")

    assert f is not None
    assert f.ticker == "NVDA"
    assert f.pe == 31.5                       # ratio: tal cual
    assert f.revenue_growth == pytest.approx(0.7068)
    assert f.gross_margin == pytest.approx(0.7415)
    assert f.debt_to_equity == 0.0433         # ratio: tal cual
    assert f.source == "finnhub"
    assert f.raw["metric"]["peTTM"] == 31.5   # crudo preservado


def test_robust_growth_overrides_ttm_artifact() -> None:
    # ETN: TTM YoY -15% (one-off) contra 3Y +9.8% y 5Y +9.0% → usa el 3Y.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metric": {
            "peTTM": 35.0,
            "revenueGrowthTTMYoy": -15.08,
            "revenueGrowth3Y": 9.77,
            "revenueGrowth5Y": 8.98,
        }})

    f = _provider(handler).fetch("ETN")
    assert f is not None
    assert f.revenue_growth == pytest.approx(0.0977)  # 3Y, no el -15% distorsionado


def test_robust_growth_keeps_strong_ttm_when_trend_agrees() -> None:
    # MU: TTM +167% (real, recuperación de memoria) y 3Y/5Y positivos → mantiene TTM.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metric": {
            "revenueGrowthTTMYoy": 166.98,
            "revenueGrowth3Y": 6.71,
            "revenueGrowth5Y": 11.76,
        }})

    f = _provider(handler).fetch("MU")
    assert f is not None
    assert f.revenue_growth == pytest.approx(1.6698)


def test_debt_prefers_annual_over_quarterly() -> None:
    # ABBV: trimestral 49.2 (equity chico) vs anual 20.2 → usa el anual, más estable.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metric": {
            "totalDebt/totalEquityAnnual": 20.19,
            "totalDebt/totalEquityQuarterly": 49.22,
        }})

    f = _provider(handler).fetch("ABBV")
    assert f is not None
    assert f.debt_to_equity == 20.19


def test_debt_falls_back_to_quarterly_without_annual() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metric": {
            "totalDebt/totalEquityQuarterly": 0.56,
        }})

    f = _provider(handler).fetch("QCOM")
    assert f is not None
    assert f.debt_to_equity == 0.56


def test_fetch_returns_none_when_no_metric() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metric": {}})

    assert _provider(handler).fetch("BOGUS") is None


def test_fetch_tolerates_missing_fields() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metric": {"peTTM": 12.0}})

    f = _provider(handler).fetch("NVDA")

    assert f is not None
    assert f.pe == 12.0
    assert f.revenue_growth is None
    assert f.gross_margin is None
    assert f.debt_to_equity is None


def test_fetch_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    with pytest.raises(FundamentalsError):
        _provider(handler).fetch("NVDA")


def test_missing_api_key_raises() -> None:
    with pytest.raises(FundamentalsError):
        FinnhubFundamentalsProvider(Settings(_env_file=None, finnhub_api_key=""))
