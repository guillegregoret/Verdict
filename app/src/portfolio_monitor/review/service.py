"""PortfolioReviewService: reevaluación integral on-demand del portfolio (/reevaluar).

Arma el contexto de TODA la cartera —cada posición con su peso, veredicto y
fundamentals, más el cash por cuenta y notas de concentración— y se lo pasa al
reasoner para una revisión holística (tesis por posición, ideas de compra/venta,
uso del cash). Read-only: no cambia estado ni ejecuta nada.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..audit import audit_verdict
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
from ..logging import get_logger
from ..reasoning import PortfolioReviewContext, ReasoningService, ReviewPosition

logger = get_logger(__name__)

# Peso (%) a partir del cual una posición se marca como concentración a vigilar.
_CONCENTRATION_PCT = 15.0
# Ventana para anotar earnings próximos junto a cada posición.
_EARNINGS_HORIZON_DAYS = 21


def _pnl_pct(price: float | None, avg_cost: float | None) -> float | None:
    """P&L no realizado (%) contra el costo promedio, o None si falta el dato."""
    if price is None or not avg_cost:
        return None
    return (price - avg_cost) / avg_cost * 100.0


def _fundamentals_text(f: FundamentalsRow | None) -> str:
    """Resumen legible de fundamentals (P/E, crecimiento, margen, D/E)."""
    if f is None:
        return "fundamentals no disponibles"
    parts = []
    if f.pe is not None:
        parts.append(f"P/E {f.pe:.1f}")
    if f.revenue_growth is not None:
        parts.append(f"crec {f.revenue_growth * 100:+.0f}%")
    if f.gross_margin is not None:
        parts.append(f"margen {f.gross_margin * 100:.0f}%")
    if f.debt_to_equity is not None:
        parts.append(f"D/E {f.debt_to_equity:.2f}")
    return ", ".join(parts) if parts else "sin métricas"


# ── Protocolos (testabilidad) ────────────────────────────────────────────────
class HoldingsReader(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...
    def shares_by_ticker(self) -> dict[str, float]: ...
    def avg_cost_by_ticker(self) -> dict[str, float]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...


class FundamentalsReader(Protocol):
    def latest(self, ticker: str) -> FundamentalsRow | None: ...


class CashReader(Protocol):
    def latest(self) -> list[AccountCashRow]: ...


class EarningsReader(Protocol):
    def upcoming(self, start: date, end: date) -> list[UpcomingEarnings]: ...


class Reviewer(Protocol):
    def review(self, context: PortfolioReviewContext) -> object: ...


class PortfolioReviewService:
    """Ensambla el portfolio completo → reasoner → texto de reevaluación."""

    def __init__(
        self,
        holdings: HoldingsReader,
        prices: PriceReader,
        fundamentals: FundamentalsReader,
        cash: CashReader,
        earnings: EarningsReader,
        reasoner: Reviewer,
    ) -> None:
        self._holdings = holdings
        self._prices = prices
        self._fundamentals = fundamentals
        self._cash = cash
        self._earnings = earnings
        self._reasoner = reasoner

    @classmethod
    def from_engine(
        cls, engine: Engine, reasoner: ReasoningService
    ) -> PortfolioReviewService:
        return cls(
            holdings=HoldingsRepository(engine),
            prices=PriceRepository(engine),
            fundamentals=FundamentalsRepository(engine),
            cash=CashRepository(engine),
            earnings=EarningsRepository(engine),
            reasoner=reasoner,
        )

    def review(self, now: datetime | None = None) -> str:
        """Devuelve el texto de la reevaluación integral (para el bot)."""
        context = self.build_context(now)
        if context is None:
            return "Sin holdings cargados todavía (esperá el próximo sync de posiciones)."
        return self.render_text(context)

    def render_text(self, context: PortfolioReviewContext) -> str:
        """Corre el reasoner sobre un contexto ya armado → texto del análisis."""
        return self._reasoner.review(context).text

    # ── Ensamblado del contexto ──────────────────────────────────────────────
    def build_context(
        self, now: datetime | None = None
    ) -> PortfolioReviewContext | None:
        """Arma el contexto de todo el portfolio (None si no hay holdings)."""
        now = now or datetime.now(UTC)
        shares = self._holdings.shares_by_ticker()
        if not shares:
            return None
        verdicts = self._holdings.verdicts_by_ticker()
        avg_costs = self._holdings.avg_cost_by_ticker()
        earnings_by_ticker = self._upcoming_earnings(now)

        rows: list[tuple[str, float, float | None]] = []  # (ticker, mv, pnl_pct)
        total = 0.0
        for ticker, sh in shares.items():
            price = self._prices.latest_price(ticker)
            mv = sh * price if price is not None else 0.0
            total += mv
            rows.append((ticker, mv, _pnl_pct(price, avg_costs.get(ticker))))
        rows.sort(key=lambda r: r[1], reverse=True)  # por valor de mercado desc

        positions: list[ReviewPosition] = []
        concentrated: list[str] = []
        audit_flags: list[str] = []
        for ticker, mv, pnl in rows:
            weight = (mv / total * 100) if total else 0.0
            if weight >= _CONCENTRATION_PCT:
                concentrated.append(f"{ticker} {weight:.0f}%")
            verdict = verdicts.get(ticker, "?")
            fundamentals = self._fundamentals.latest(ticker)
            audit = audit_verdict(ticker, verdict, fundamentals)
            if audit is not None:
                audit_flags.append(f"{audit.ticker} [{audit.verdict}]: {audit.issue}")
            positions.append(
                ReviewPosition(
                    ticker=ticker,
                    weight=weight,
                    verdict=verdict,
                    market_value=mv,
                    fundamentals_text=_fundamentals_text(fundamentals),
                    earnings=earnings_by_ticker.get(ticker),
                    unrealized_pct=pnl,
                    audit=audit.issue if audit is not None else None,
                )
            )

        cash_rows = self._cash.latest()
        total_cash = sum(r.available_funds for r in cash_rows)
        cash_block = "\n".join(
            f"• {r.name}: ${r.available_funds:,.0f} {r.currency}" for r in cash_rows
        )
        note = (
            "posiciones pesadas → " + ", ".join(concentrated)
            if concentrated else None
        )
        return PortfolioReviewContext(
            positions_block="\n".join(self._position_line(p) for p in positions),
            cash_block=cash_block,
            total_value=total,
            total_cash=total_cash,
            position_count=len(positions),
            note=note,
            positions=tuple(positions),
            cash_accounts=tuple(
                (r.name, r.available_funds, r.currency) for r in cash_rows
            ),
            audit_flags=tuple(audit_flags),
        )

    def _position_line(self, p: ReviewPosition) -> str:
        earn = f" · 📅 earnings {p.earnings:%d/%m}" if p.earnings else ""
        pnl = f" · P&L {p.unrealized_pct:+.1f}%" if p.unrealized_pct is not None else ""
        audit = f" · ⚠ {p.audit}" if p.audit else ""
        return (
            f"• {p.ticker} {p.weight:.1f}% [{p.verdict}]{pnl} — "
            f"{p.fundamentals_text}{earn}{audit}"
        )

    def _upcoming_earnings(self, now: datetime) -> dict[str, date]:
        rows = self._earnings.upcoming(
            now.date(), now.date() + timedelta(days=_EARNINGS_HORIZON_DAYS)
        )
        out: dict[str, date] = {}
        for r in rows:
            out.setdefault(r.ticker, r.earnings_date)  # el más próximo (vienen ordenados)
        return out
