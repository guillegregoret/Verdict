"""Tests del bot de Telegram: seguridad (allowlist fail-closed) + router."""

from __future__ import annotations

from datetime import UTC, date, datetime

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import (
    AccountCashRow,
    FundamentalsRow,
    UpcomingEarnings,
)
from portfolio_monitor.telegram_bot import CommandRouter, TelegramBot
from portfolio_monitor.telegram_bot.bot import _parse_ids

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


# ── Seguridad del bot ────────────────────────────────────────────────────────
class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def handle(self, text: str) -> str:
        self.calls.append(text)
        return f"resp:{text}"


def _bot(allowed: str = "", router: FakeRouter | None = None) -> TelegramBot:
    return TelegramBot(
        Settings(
            _env_file=None, telegram_bot_token="t", telegram_allowed_user_ids=allowed
        ),
        router or FakeRouter(),
    )


def _update(text: str, user_id: int = 111, chat_id: int = -999) -> dict:
    return {
        "update_id": 1,
        "message": {"text": text, "chat": {"id": chat_id}, "from": {"id": user_id}},
    }


def test_parse_ids_tolerates_junk() -> None:
    assert _parse_ids("111, 222 ,x, 333") == frozenset({111, 222, 333})


def test_whoami_available_to_anyone() -> None:
    r = _bot(allowed="").response_for(_update("/whoami", user_id=111))
    assert r is not None
    assert "111" in r[1]


def test_failclosed_blocks_data_when_no_allowlist() -> None:
    router = FakeRouter()
    r = _bot(allowed="", router=router).response_for(_update("/status", user_id=111))
    assert r is not None
    assert "bloqueado" in r[1].lower()
    assert router.calls == []  # NO ejecutó el comando


def test_authorized_user_gets_response() -> None:
    router = FakeRouter()
    r = _bot(allowed="111", router=router).response_for(_update("/status", user_id=111))
    assert r == (-999, "resp:/status")
    assert router.calls == ["/status"]


def test_unauthorized_user_ignored_when_allowlist_set() -> None:
    router = FakeRouter()
    # allowlist tiene 111, pero escribe 222 → ignorado en silencio
    r = _bot(allowed="111", router=router).response_for(_update("/status", user_id=222))
    assert r is None
    assert router.calls == []


def test_non_command_ignored() -> None:
    assert _bot(allowed="111").response_for(_update("hola", user_id=111)) is None


# ── CommandRouter ────────────────────────────────────────────────────────────
class FakeCash:
    def latest(self) -> list[AccountCashRow]:
        return [
            AccountCashRow("U1", "Satélite IA", 68.0, 68.0, "USD"),
            AccountCashRow("U2", "Salud/Defensa", 495.0, 495.0, "USD"),
        ]


class FakeHoldings:
    def verdicts_by_ticker(self) -> dict[str, str]:
        return {"NVDA": "Mantener", "GOOG": "Crecer"}

    def shares_by_ticker(self) -> dict[str, float]:
        return {"NVDA": 10.0, "GOOG": 5.0}


class FakePrices:
    def latest_price(self, ticker: str) -> float | None:
        return {"NVDA": 200.0, "GOOG": 180.0}.get(ticker)


class FakeEarnings:
    def upcoming(self, start: date, end: date) -> list[UpcomingEarnings]:
        return [UpcomingEarnings("NVDA", date(2026, 7, 20), "amc", 1.79, "Mantener")]


class FakeFundamentals:
    def latest(self, ticker: str) -> FundamentalsRow | None:
        if ticker != "NVDA":
            return None
        return FundamentalsRow("NVDA", NOW, 31.5, 0.70, 0.74, 0.04)


def _router() -> CommandRouter:
    return CommandRouter(
        FakeCash(), FakeHoldings(), FakePrices(), FakeEarnings(), FakeFundamentals()
    )


def test_router_help() -> None:
    assert "/status" in _router().handle("/help")


def test_router_cash() -> None:
    out = _router().handle("/cash")
    assert "Satélite IA" in out and "$68" in out
    assert "Total: $563" in out


def test_router_status() -> None:
    out = _router().handle("/status", now=NOW)
    # valor = 10*200 + 5*180 = 2900; cash 563; 2 posiciones
    assert "$2,900" in out
    assert "$563" in out
    assert "Posiciones: 2" in out


def test_router_earnings() -> None:
    out = _router().handle("/earnings", now=NOW)
    assert "NVDA" in out


def test_router_ticker_detail() -> None:
    out = _router().handle("/nvda")
    assert "NVDA" in out
    assert "Mantener" in out
    assert "$200" in out
    assert "P/E 31.5" in out


def test_router_unknown_command() -> None:
    out = _router().handle("/xyz")
    assert "No entendí" in out
