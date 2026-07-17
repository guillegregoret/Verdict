"""Modelos del módulo de razonamiento (§5.6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db.repositories import FundamentalsRow
from ..trigger import TriggerEvent

if TYPE_CHECKING:
    from ..fundamentals import FundamentalsEvent
    from ..ratings import RatingEvent


@dataclass(frozen=True)
class ReasoningContext:
    """Todo lo que necesita el reasoner para armar una sugerencia (§5.6).

    Cubre dos tipos de señal (`signal_kind`):
    - "price_move": caída/suba de precio (compra en dip, tomar ganancias, consolidar).
    - "fundamentals_decay": deterioro de la tesis (revisar), con `note` explicando qué empeoró.
    """

    ticker: str
    verdict: str
    signal_kind: str = "price_move"   # "price_move" | "fundamentals_decay"
    action: str = "comprar_dip"
    pct_change: float = 0.0
    window_minutes: int = 0
    current_price: float = 0.0
    reference_price: float = 0.0
    note: str | None = None           # qué se deterioró (para fundamentals_decay)
    fundamentals: FundamentalsRow | None = None
    bucket_remaining: float | None = None   # cash disponible en la cuenta (§5.4)
    dca_suggested_usd: float | None = None  # monto de DCA sugerido en el dip (§5.4)

    @classmethod
    def from_trigger_event(
        cls,
        event: TriggerEvent,
        fundamentals: FundamentalsRow | None = None,
        bucket_remaining: float | None = None,
        dca_suggested_usd: float | None = None,
    ) -> ReasoningContext:
        """Contexto de una señal de precio a partir de un TriggerEvent (§11.4)."""
        return cls(
            ticker=event.ticker,
            verdict=event.verdict,
            signal_kind="price_move",
            action=event.action,
            pct_change=event.pct_change,
            window_minutes=event.window_minutes,
            current_price=event.current_price,
            reference_price=event.reference_price,
            fundamentals=fundamentals,
            bucket_remaining=bucket_remaining,
            dca_suggested_usd=dca_suggested_usd,
        )

    @classmethod
    def from_fundamentals_event(cls, event: FundamentalsEvent) -> ReasoningContext:
        """Contexto de una señal de deterioro de fundamentals (§5.3)."""
        return cls(
            ticker=event.ticker,
            verdict=event.verdict,
            signal_kind="fundamentals_decay",
            action="revisar_tesis",
            note="; ".join(event.reasons),
            fundamentals=event.current,
        )

    @classmethod
    def from_rating_event(cls, event: RatingEvent) -> ReasoningContext:
        """Contexto de una señal de cambio de rating de analistas (§5)."""
        return cls(
            ticker=event.ticker,
            verdict=event.verdict,
            signal_kind="rating_shift",
            action="revisar_tesis",
            note=event.note,
        )


@dataclass(frozen=True)
class Suggestion:
    """Sugerencia lista para notificar. `source` = 'anthropic' | 'template'."""

    text: str
    source: str


@dataclass(frozen=True)
class MonitorSignal:
    """Señal lista para el pipeline: contexto + tipo (para auditoría/cooldown)."""

    context: ReasoningContext
    trigger_type: str
