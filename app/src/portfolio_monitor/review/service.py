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
from ..reasoning import PortfolioReviewContext, ReasoningService

logger = get_logger(__name__)

# Peso (%) a partir del cual una posición se marca como concentración a vigilar.
_CONCENTRATION_PCT = 15.0
# Ventana para anotar earnings próximos junto a cada posición.
_EARNINGS_HORIZON_DAYS = 21


# ── Protocolos (testabilidad) ────────────────────────────────────────────────
class HoldingsReader(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...
    def shares_by_ticker(self) -> dict[str, float]: ...


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
        now = now or datetime.now(UTC)
        context = self._build_context(now)
        if context is None:
            return "Sin holdings cargados todavía (esperá el próximo sync de posiciones)."
        return self._reasoner.review(context).text

    # ── Ensamblado del contexto ──────────────────────────────────────────────
    def _build_context(self, now: datetime) -> PortfolioReviewContext | None:
        shares = self._holdings.shares_by_ticker()
        if not shares:
            return None
        verdicts = self._holdings.verdicts_by_ticker()
        earnings_by_ticker = self._upcoming_earnings(now)

        rows: list[tuple[str, float, float]] = []  # (ticker, shares, market_value)
        total = 0.0
        for ticker, sh in shares.items():
            price = self._prices.latest_price(ticker)
            mv = sh * price if price is not None else 0.0
            total += mv
            rows.append((ticker, sh, mv))
        rows.sort(key=lambda r: r[2], reverse=True)  # por valor de mercado desc

        lines: list[str] = []
        concentrated: list[str] = []
        for ticker, _sh, mv in rows:
            weight = (mv / total * 100) if total else 0.0
            if weight >= _CONCENTRATION_PCT:
                concentrated.append(f"{ticker} {weight:.0f}%")
            lines.append(
                self._position_line(
                    ticker, weight, verdicts.get(ticker, "?"),
                    earnings_by_ticker.get(ticker),
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
            positions_block="\n".join(lines),
            cash_block=cash_block,
            total_value=total,
            total_cash=total_cash,
            position_count=len(rows),
            note=note,
        )

    def _position_line(
        self, ticker: str, weight: float, verdict: str, earnings: date | None
    ) -> str:
        f = self._fundamentals.latest(ticker)
        if f is not None:
            parts = []
            if f.pe is not None:
                parts.append(f"P/E {f.pe:.1f}")
            if f.revenue_growth is not None:
                parts.append(f"crec {f.revenue_growth * 100:+.0f}%")
            if f.gross_margin is not None:
                parts.append(f"margen {f.gross_margin * 100:.0f}%")
            if f.debt_to_equity is not None:
                parts.append(f"D/E {f.debt_to_equity:.2f}")
            fund = ", ".join(parts) if parts else "sin métricas"
        else:
            fund = "fundamentals no disponibles"
        earn = f" · 📅 earnings {earnings:%d/%m}" if earnings else ""
        return f"• {ticker} {weight:.1f}% [{verdict}] — {fund}{earn}"

    def _upcoming_earnings(self, now: datetime) -> dict[str, date]:
        rows = self._earnings.upcoming(
            now.date(), now.date() + timedelta(days=_EARNINGS_HORIZON_DAYS)
        )
        out: dict[str, date] = {}
        for r in rows:
            out.setdefault(r.ticker, r.earnings_date)  # el más próximo (vienen ordenados)
        return out
