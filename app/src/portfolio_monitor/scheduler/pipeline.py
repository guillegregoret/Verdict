"""AlertPipeline: encadena trigger → fundamentals → reasoning → notifier (§2).

Por cada TriggerEvent accionable: trae los últimos fundamentals, arma el contexto,
genera la sugerencia, la manda por Telegram y —solo si el envío fue exitoso—
registra la alerta (lo que activa el cooldown, §5.5). Read-only sobre el mercado.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine

from ..db.repositories import (
    AlertRepository,
    FundamentalsRepository,
    FundamentalsRow,
)
from ..logging import get_logger
from ..notifier import NotifierError
from ..reasoning import ReasoningContext, ReasoningError, ReasoningService, Suggestion
from ..trigger import TriggerEngine, TriggerEvent

logger = get_logger(__name__)


# ── Protocolos para desacoplar de las clases concretas (testabilidad) ────────
class TriggerSource(Protocol):
    def evaluate(self) -> list[TriggerEvent]: ...


class FundamentalsReader(Protocol):
    def latest(self, ticker: str) -> FundamentalsRow | None: ...


class SuggestionMaker(Protocol):
    def suggest(self, context: ReasoningContext) -> Suggestion: ...


class MessageSender(Protocol):
    def send(self, text: str) -> None: ...


class AlertSink(Protocol):
    def record(
        self,
        ticker: str,
        trigger_type: str,
        pct_change: float,
        window_minutes: int,
        verdict: str,
        suggestion: str | None = ...,
        bucket_remaining: float | None = ...,
    ) -> int: ...


class AlertPipeline:
    """Orquesta la evaluación de triggers hasta la notificación."""

    def __init__(
        self,
        trigger: TriggerSource,
        fundamentals: FundamentalsReader,
        reasoning: SuggestionMaker,
        notifier: MessageSender,
        alerts: AlertSink,
    ) -> None:
        self._trigger = trigger
        self._fundamentals = fundamentals
        self._reasoning = reasoning
        self._notifier = notifier
        self._alerts = alerts

    @classmethod
    def from_engine(
        cls, engine: Engine, reasoning: ReasoningService, notifier: MessageSender
    ) -> AlertPipeline:
        """Cablea los repos y el trigger engine sobre `engine`."""
        return cls(
            trigger=TriggerEngine.from_engine(engine),
            fundamentals=FundamentalsRepository(engine),
            reasoning=reasoning,
            notifier=notifier,
            alerts=AlertRepository(engine),
        )

    def run_once(self) -> int:
        """Un ciclo. Devuelve cuántas alertas se enviaron."""
        events = self._trigger.evaluate()
        sent = 0
        for event in events:
            fundamentals = self._fundamentals.latest(event.ticker)
            context = ReasoningContext.from_trigger_event(
                event, fundamentals=fundamentals
            )

            try:
                suggestion = self._reasoning.suggest(context)
            except ReasoningError as exc:
                logger.warning("Reasoning falló para %s: %s", event.ticker, exc)
                continue

            try:
                self._notifier.send(suggestion.text)
            except NotifierError as exc:
                logger.warning("Notificación falló para %s: %s", event.ticker, exc)
                continue  # no registramos la alerta si no se envió (cooldown intacto)

            self._alerts.record(
                ticker=event.ticker,
                trigger_type=event.trigger_type,
                pct_change=event.pct_change,
                window_minutes=event.window_minutes,
                verdict=event.verdict,
                suggestion=suggestion.text,
            )
            sent += 1

        if events:
            logger.info("Pipeline: %d/%d alertas enviadas.", sent, len(events))
        return sent
