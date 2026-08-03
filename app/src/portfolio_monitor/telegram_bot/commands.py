"""CommandRouter: comandos read-only del bot (§5).

Traduce un comando (`/status`, `/cash`, `/earnings`, `/<ticker>`, `/help`) a un
texto de respuesta consultando la DB. No cambia estado ni ejecuta nada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..data.ibc import GatewayControl, GatewayControlError
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
from ..holdings import HoldingsSyncService
from ..logging import get_logger
from ..monitoring import HealthService
from ..reasoning import ReasoningService
from ..report import ReportService

logger = get_logger(__name__)

_HELP = (
    "Comandos:\n"
    "/status — resumen del portfolio y cash\n"
    "/reevaluar — reevaluación integral: sincroniza con IBKR y arma tesis, "
    "pesos, ideas (tarda ~30s)\n"
    "/cash — cash disponible por cuenta\n"
    "/earnings — próximos earnings (30 días)\n"
    "/health — estado de componentes (DB, Finnhub, IBKR) y frescura de datos\n"
    "/reconnect — pide al gateway re-loguearse a IBKR (te llega el 2FA al cel)\n"
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


class ReportLike(Protocol):
    def deliver(self, now: datetime | None = ...) -> str: ...


class SyncLike(Protocol):
    def run_once(self) -> int: ...


class HealthLike(Protocol):
    def render(self, now: datetime | None = ...) -> str: ...


class GatewayLike(Protocol):
    def restart(self) -> str: ...


class CommandRouter:
    """Despacha comandos del bot a respuestas de texto (read-only)."""

    def __init__(
        self,
        cash: CashReader,
        holdings: HoldingsReader,
        prices: PriceReader,
        earnings: EarningsReader,
        fundamentals: FundamentalsReader,
        report: ReportLike | None = None,
        sync: SyncLike | None = None,
        health: HealthLike | None = None,
        gateway: GatewayLike | None = None,
    ) -> None:
        self._cash = cash
        self._holdings = holdings
        self._prices = prices
        self._earnings = earnings
        self._fundamentals = fundamentals
        self._report = report
        self._sync = sync
        self._health = health
        self._gateway = gateway

    @classmethod
    def from_engine(
        cls,
        engine: Engine,
        reasoning: ReasoningService | None = None,
        settings: Settings | None = None,
    ) -> CommandRouter:
        settings = settings or Settings()
        return cls(
            cash=CashRepository(engine),
            holdings=HoldingsRepository(engine),
            prices=PriceRepository(engine),
            earnings=EarningsRepository(engine),
            fundamentals=FundamentalsRepository(engine),
            report=(
                ReportService.from_engine(engine, reasoning, settings)
                if reasoning is not None
                else None
            ),
            sync=HoldingsSyncService.from_engine(settings, engine),
            health=HealthService(engine),
            gateway=GatewayControl(settings),
        )

    def handle(self, text: str, now: datetime | None = None) -> str:
        """Comando → respuesta. `now` inyectable para tests."""
        now = now or datetime.now(UTC)
        cmd = text.strip().split()[0].lstrip("/").split("@")[0].lower()
        if cmd in ("help", "start"):
            return _HELP
        if cmd == "status":
            return self._status(now)
        if cmd in ("reevaluar", "review"):
            if self._report is None:
                return "Reevaluación no disponible (falta configurar el reasoner)."
            warning = self._refresh_holdings()
            return warning + self._report.deliver(now)
        if cmd == "cash":
            return self._cash_report()
        if cmd == "earnings":
            return self._earnings_report(now)
        if cmd == "health":
            if self._health is None:
                return "Health no disponible."
            return self._health.render(now)
        if cmd == "reconnect":
            return self._reconnect()
        # /<ticker>
        detail = self._ticker(cmd.upper())
        return detail if detail is not None else f"No entendí «{cmd}». Probá /help."

    def _refresh_holdings(self) -> str:
        """Sync on-demand de posiciones/cash antes del reporte (best-effort).

        `/reevaluar` a mano debe ver el estado actual, no el último sync periódico
        (~30 min). Si el gateway no está disponible, se usa el último snapshot y se
        antepone un aviso — nunca se rompe el reporte por esto.
        """
        if self._sync is None:
            return ""
        try:
            applied = self._sync.run_once()
        except Exception as exc:  # noqa: BLE001 - best-effort, no debe romper el reporte
            logger.warning("Sync on-demand de /reevaluar falló (%s).", exc)
            applied = 0
        if applied <= 0:
            return (
                "⚠️ No pude sincronizar con IBKR ahora: uso el último snapshot "
                "guardado (puede estar desactualizado).\n\n"
            )
        return ""

    def _reconnect(self) -> str:
        """Pide al gateway re-loguearse a IBKR (dispara el 2FA al celular)."""
        if self._gateway is None:
            return "Reconnect no disponible (gateway no configurado)."
        try:
            resp = self._gateway.restart()
        except GatewayControlError as exc:
            return (
                f"⚠️ No pude pedirle al gateway que se reconecte: {exc}\n"
                "Si el command server de IBC no está habilitado, reiniciá el "
                "gateway a mano: `docker compose restart ib-gateway`."
            )
        return (
            "🔄 Le pedí al gateway que se re-loguee a IBKR. Vas a recibir el push "
            "de 2FA en tu celular — aprobalo y en ~1-2 min las posiciones y el cash "
            "se sincronizan. Chequealo con /health.\n\n"
            f"Respuesta del gateway: {resp}"
        )

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
