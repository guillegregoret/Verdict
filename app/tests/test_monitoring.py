"""Tests del módulo de monitoreo con httpx.MockTransport (sin red)."""

from __future__ import annotations

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.monitoring import (
    HealthcheckPinger,
    MonitoringError,
    StatusPagePoller,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── HealthcheckPinger ────────────────────────────────────────────────────────
def test_ping_success_hits_base_url() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="OK")

    settings = Settings(_env_file=None, healthchecks_ping_url="https://hc.io/abc")
    HealthcheckPinger(settings, client=_client(handler)).ping(success=True)
    assert seen["url"] == "https://hc.io/abc"


def test_ping_failure_hits_fail_endpoint() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="OK")

    settings = Settings(_env_file=None, healthchecks_ping_url="https://hc.io/abc/")
    HealthcheckPinger(settings, client=_client(handler)).ping(success=False)
    assert seen["url"] == "https://hc.io/abc/fail"


def test_ping_failure_kuma_push_uses_status_down() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="OK")

    settings = Settings(
        _env_file=None,
        healthchecks_ping_url="http://uptime-kuma:3001/api/push/tok123",
    )
    HealthcheckPinger(settings, client=_client(handler)).ping(success=False)
    assert seen["url"] == "http://uptime-kuma:3001/api/push/tok123?status=down"


def test_ping_is_noop_without_url() -> None:
    called = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200)

    pinger = HealthcheckPinger(Settings(_env_file=None), client=_client(handler))
    assert pinger.enabled is False
    pinger.ping(success=True)
    assert called["n"] == 0


def test_ping_never_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    settings = Settings(_env_file=None, healthchecks_ping_url="https://hc.io/abc")
    # no debe propagar
    HealthcheckPinger(settings, client=_client(handler)).ping(success=True)


# ── StatusPagePoller ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "indicator, expected",
    [
        ("none", "up"),
        ("minor", "degraded"),
        ("major", "degraded"),
        ("critical", "down"),
        ("desconocido", "degraded"),
    ],
)
def test_status_poll_maps_indicator(indicator: str, expected: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": {"indicator": indicator}})

    result = StatusPagePoller(client=_client(handler)).poll("https://x/status.json")
    assert result == expected


def test_status_poll_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    with pytest.raises(MonitoringError):
        StatusPagePoller(client=_client(handler)).poll("https://x/status.json")
