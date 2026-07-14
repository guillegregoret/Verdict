"""Modelos del módulo de razonamiento (§5.6)."""

from __future__ import annotations

from dataclasses import dataclass

from ..db.repositories import FundamentalsRow
from ..trigger import TriggerEvent


@dataclass(frozen=True)
class ReasoningContext:
    """Todo lo que necesita el reasoner para armar una sugerencia (§5.6)."""

    ticker: str
    pct_change: float
    window_minutes: int
    verdict: str
    current_price: float
    reference_price: float
    action: str = "comprar_dip"  # comprar_dip | tomar_ganancias | consolidar
    fundamentals: FundamentalsRow | None = None
    bucket_remaining: float | None = None

    @classmethod
    def from_trigger_event(
        cls,
        event: TriggerEvent,
        fundamentals: FundamentalsRow | None = None,
        bucket_remaining: float | None = None,
    ) -> ReasoningContext:
        """Construye el contexto a partir de un TriggerEvent (§11.4) + fundamentals."""
        return cls(
            ticker=event.ticker,
            pct_change=event.pct_change,
            window_minutes=event.window_minutes,
            verdict=event.verdict,
            current_price=event.current_price,
            reference_price=event.reference_price,
            action=event.action,
            fundamentals=fundamentals,
            bucket_remaining=bucket_remaining,
        )


@dataclass(frozen=True)
class Suggestion:
    """Sugerencia lista para notificar. `source` = 'anthropic' | 'template'."""

    text: str
    source: str
