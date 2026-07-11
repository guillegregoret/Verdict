"""Tests del notifier de Telegram con httpx.MockTransport (sin red)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.notifier import NotifierError, TelegramNotifier
from portfolio_monitor.reasoning import Suggestion


def _settings() -> Settings:
    return Settings(_env_file=None, telegram_bot_token="T0KEN", telegram_chat_id="42")


def _notifier(handler: Callable[[httpx.Request], httpx.Response]) -> TelegramNotifier:
    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    return TelegramNotifier(_settings(), client=http)


def test_send_posts_to_send_message() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    _notifier(handler).send("NVDA cayó 5%")

    assert seen["path"] == "/botT0KEN/sendMessage"
    assert seen["body"]["chat_id"] == "42"
    assert seen["body"]["text"] == "NVDA cayó 5%"


def test_notify_sends_suggestion_text() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json={"ok": True, "result": {}})

    _notifier(handler).notify(Suggestion(text="Podés sumar ~$300", source="template"))
    assert seen["body"]["text"] == "Podés sumar ~$300"


def test_send_raises_on_http_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    with pytest.raises(NotifierError):
        _notifier(handler).send("hola")


def test_send_raises_when_ok_false() -> None:
    # 200 pero ok=false (defensa extra)
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "boom"})

    with pytest.raises(NotifierError):
        _notifier(handler).send("hola")


def test_missing_credentials_raises() -> None:
    with pytest.raises(NotifierError):
        TelegramNotifier(Settings(_env_file=None, telegram_bot_token="", telegram_chat_id=""))
