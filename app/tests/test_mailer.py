"""Tests del ResendMailer con un cliente httpx fake (sin red)."""

from __future__ import annotations

import httpx
import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.mailer import MailerError, ResendMailer


def _settings(**over: object) -> Settings:
    base = dict(
        _env_file=None,
        email_enabled=True,
        resend_api_key="re_test",
        email_from="Verdict <verdict@mail.example.com>",
        email_to="me@example.com",
    )
    base.update(over)
    return Settings(**base)


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.posts: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> _Resp:
        self.posts.append((url, json))
        return _Resp(self.status)


def test_requires_configuration() -> None:
    with pytest.raises(MailerError):
        ResendMailer(Settings(_env_file=None))  # email no configurado


def test_send_posts_expected_payload() -> None:
    client = _FakeClient()
    mailer = ResendMailer(_settings(), client=client)  # type: ignore[arg-type]

    mailer.send("Subject", "<h1>hi</h1>", text="hi")

    url, payload = client.posts[0]
    assert url == "/emails"
    assert payload["from"] == "Verdict <verdict@mail.example.com>"
    assert payload["to"] == ["me@example.com"]
    assert payload["subject"] == "Subject"
    assert payload["html"] == "<h1>hi</h1>"
    assert payload["text"] == "hi"
    assert mailer.recipient == "me@example.com"


def test_send_wraps_http_errors() -> None:
    mailer = ResendMailer(_settings(), client=_FakeClient(status=500))  # type: ignore[arg-type]
    with pytest.raises(MailerError):
        mailer.send("s", "<b>x</b>")


def test_email_configured_flag() -> None:
    assert _settings().email_configured is True
    assert _settings(resend_api_key="").email_configured is False
    assert _settings(email_enabled=False).email_configured is False
