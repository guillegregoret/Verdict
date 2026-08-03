"""Tests del cliente del command server de IBC (/reconnect), sin red."""

from __future__ import annotations

import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.data.ibc import GatewayControl, GatewayControlError


class FakeSocket:
    def __init__(self, response: bytes = b"OK Gateway restart\n") -> None:
        self._response = response
        self.sent: list[bytes] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, bufsize: int) -> bytes:
        return self._response

    def close(self) -> None:
        self.closed = True


def _settings(enabled: bool = True) -> Settings:
    return Settings(_env_file=None, ib_gateway_command_enabled=enabled)


def test_restart_sends_command_and_returns_response() -> None:
    sock = FakeSocket(b"OK restarting\n")
    captured: dict = {}

    def connect(host, port, timeout):
        captured.update(host=host, port=port, timeout=timeout)
        return sock

    out = GatewayControl(_settings(), connect=connect).restart()

    assert sock.sent == [b"RESTART\n"]
    assert sock.closed is True           # se cierra siempre
    assert out == "OK restarting"
    assert captured["port"] == 7462      # default del command server


def test_disabled_command_server_raises() -> None:
    with pytest.raises(GatewayControlError, match="no está habilitado"):
        GatewayControl(_settings(enabled=False)).restart()


def test_connection_failure_raises_gateway_error() -> None:
    def connect(host, port, timeout):
        raise OSError("connection refused")

    with pytest.raises(GatewayControlError, match="No se pudo conectar"):
        GatewayControl(_settings(), connect=connect).restart()


def test_empty_response_is_handled() -> None:
    out = GatewayControl(_settings(), connect=lambda *a: FakeSocket(b"")).restart()
    assert "RESTART enviado" in out
