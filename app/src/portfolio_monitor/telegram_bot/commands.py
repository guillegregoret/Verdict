"""CommandRouter: comandos read-only del bot (§5).

Traduce un comando (`/status`, `/cash`, `/earnings`, `/<ticker>`, `/help`) a un
texto de respuesta consultando la DB. No cambia estado ni ejecuta nada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..db.repositories import (
    AccountCashRow,
    CashRepository,
    EarningsRepository,
    FundamentalsRepository,
    FundamentalsRow,
    HoldingsRepository,
    PriceRepository,
    UpcomingEarnings,
)

_HELP = (
    "Comandos:\n"
    "/status — resumen del portfolio y cash\n"
    "/cash — cash disponible por cuenta\n"
    "/earnings — próximos earnings (30 días)\n"
    "/<ticker> — detalle de un ticker (ej: /nvda)\n"
    "/whoami — tu id de Telegram\n"
    "/help — esta ayuda"
)


# ── Protocolos (testabilidad) ────────────────────────────────────────────────
class CashReader(Protocol):
    def latest(self) -> list[AccountCashRow]: ...


class HoldingsReader(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...
    def shares_by_ticker(self) -> dict[str, float]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...


class EarningsReader(Protocol):
    def upcoming(self, start, end) -> list[UpcomingEarnings]: ...


class FundamentalsReader(Protocol):
    def latest(self, ticker: str) -> FundamentalsRow | None: ...


class CommandRouter:
    """Despacha comandos del bot a respuestas de texto (read-only)."""

    def __init__(
        self,
        cash: CashReader,
        holdings: HoldingsReader,
        prices: PriceReader,
        earnings: EarningsReader,
        fundamentals: FundamentalsReader,
    ) -> None:
        self._cash = cash
        self._holdings = holdings
        self._prices = prices
        self._earnings = earnings
        self._fundamentals = fundamentals

    @classmethod
    def from_engine(cls, engine: Engine) -> CommandRouter:
        return cls(
            cash=CashRepository(engine),
            holdings=HoldingsRepository(engine),
            prices=PriceRepository(engine),
            earnings=EarningsRepository(engine),
            fundamentals=FundamentalsRepository(engine),
        )

    def handle(self, text: str, now: datetime | None = None) -> str:
        """Comando → respuesta. `now` inyectable para tests."""
        now = now or datetime.now(UTC)
        cmd = text.strip().split()[0].lstrip("/").split("@")[0].lower()
        if cmd in ("help", "start"):
            return _HELP
        if cmd == "status":
            return self._status(now)
        if cmd == "cash":
            return self._cash_report()
        if cmd == "earnings":
            return self._earnings_report(now)
        # /<ticker>
        detail = self._ticker(cmd.upper())
        return detail if detail is not None else f"No entendí «{cmd}». Probá /help."

    # ── Reportes ─────────────────────────────────────────────────────────────
    def _cash_report(self) -> str:
        rows = self._cash.latest()
        if not rows:
            return "Sin datos de cash todavía (esperá el próximo sync de holdings)."
        lines = ["💵 Cash disponible por cuenta:"]
        total = 0.0
        for r in rows:
            total += r.available_funds
            lines.append(f"• {r.name}: ${r.available_funds:,.0f} {r.currency}")
        lines.append(f"Total: ${total:,.0f}")
        return "\n".join(lines)

    def _status(self, now: datetime) -> str:
        shares = self._holdings.shares_by_ticker()
        market_value = 0.0
        for ticker, sh in shares.items():
            price = self._prices.latest_price(ticker)
            if price is not None:
                market_value += sh * price
        cash_rows = self._cash.latest()
        total_cash = sum(r.available_funds for r in cash_rows)

        lines = [
            "📊 Estado del portfolio",
            f"Valor de mercado: ${market_value:,.0f}",
            f"Cash disponible: ${total_cash:,.0f}",
            f"Posiciones: {len(shares)}",
        ]
        upcoming = self._earnings.upcoming(now.date(), now.date() + timedelta(days=14))
        if upcoming:
            nxt = upcoming[0]
            lines.append(f"Próximo earnings: {nxt.ticker} {nxt.earnings_date:%d/%m}")
        return "\n".join(lines)

    def _earnings_report(self, now: datetime) -> str:
        rows = self._earnings.upcoming(now.date(), now.date() + timedelta(days=30))
        if not rows:
            return "Sin earnings en los próximos 30 días."
        lines = ["📅 Próximos earnings (30 días):"]
        for r in rows[:20]:
            verdict = f" [{r.verdict}]" if r.verdict else ""
            lines.append(f"• {r.earnings_date:%d/%m} — {r.ticker}{verdict}")
        return "\n".join(lines)

    def _ticker(self, ticker: str) -> str | None:
        verdicts = self._holdings.verdicts_by_ticker()
        price = self._prices.latest_price(ticker)
        if ticker not in verdicts and price is None:
            return None  # no es un ticker conocido → comando desconocido

        lines = [f"🔎 {ticker}"]
        if ticker in verdicts:
            lines.append(f"Veredicto: {verdicts[ticker]}")
        if price is not None:
            lines.append(f"Último precio: ${price:,.2f}")
        f = self._fundamentals.latest(ticker)
        if f is not None:
            parts = []
            if f.pe is not None:
                parts.append(f"P/E {f.pe:.1f}")
            if f.revenue_growth is not None:
                parts.append(f"crec. {f.revenue_growth * 100:+.0f}%")
            if f.gross_margin is not None:
                parts.append(f"margen {f.gross_margin * 100:.0f}%")
            if parts:
                lines.append("Fundamentals: " + ", ".join(parts))
        return "\n".join(lines)
