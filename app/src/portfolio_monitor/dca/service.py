"""DcaSizer: cuánto sugerir comprar en un dip, capado al cash (§5.4).

Monto = tranche base × multiplicador (crece con la profundidad de la caída,
topeado), pero nunca más que el cash disponible de la cuenta que tiene el ticker.
El tranche/tope salen del plan del ticker (dca_plan) o de los defaults de Settings.
🔴 Solo sugiere; el usuario ejecuta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..db.repositories import (
    CashRepository,
    DcaPlan,
    DcaPlanRepository,
    HoldingsRepository,
)


@dataclass(frozen=True)
class DcaSuggestion:
    """Sugerencia de monto a comprar en un dip."""

    ticker: str
    amount_usd: float       # sugerido, ya capado al cash disponible
    available_cash: float   # cash de la cuenta (bucket, §5.4)
    tranche_usd: float      # monto base usado
    multiplier: float       # factor aplicado por la profundidad del dip


class PlanSource(Protocol):
    def plans_by_ticker(self) -> dict[str, DcaPlan]: ...


class CashSource(Protocol):
    def latest_available(self) -> dict[str, float]: ...


class AccountSource(Protocol):
    def account_by_ticker(self) -> dict[str, str]: ...


class DcaSizer:
    """Calcula el monto de DCA sugerido para un dip."""

    def __init__(
        self,
        plans: PlanSource,
        cash: CashSource,
        accounts: AccountSource,
        settings: Settings,
    ) -> None:
        self._plans = plans
        self._cash = cash
        self._accounts = accounts
        self._settings = settings

    @classmethod
    def from_engine(cls, engine: Engine, settings: Settings) -> DcaSizer:
        return cls(
            plans=DcaPlanRepository(engine),
            cash=CashRepository(engine),
            accounts=HoldingsRepository(engine),
            settings=settings,
        )

    def size(self, ticker: str, pct_change: float) -> DcaSuggestion | None:
        """Sugerencia de DCA para un dip (None si el movimiento no es una caída)."""
        if pct_change >= 0:
            return None
        s = self._settings
        plan = self._plans.plans_by_ticker().get(ticker)
        tranche = plan.tranche_usd if plan else s.dca_default_tranche_usd
        max_mult = plan.max_multiplier if plan else s.dca_max_multiplier

        multiplier = min(1.0 + abs(pct_change) * s.dca_dip_slope, max_mult)
        ibkr_id = self._accounts.account_by_ticker().get(ticker)
        available = self._cash.latest_available().get(ibkr_id, 0.0) if ibkr_id else 0.0
        amount = min(tranche * multiplier, max(available, 0.0))

        return DcaSuggestion(
            ticker=ticker,
            amount_usd=round(max(amount, 0.0), 2),
            available_cash=round(available, 2),
            tranche_usd=round(tranche, 2),
            multiplier=round(multiplier, 2),
        )
