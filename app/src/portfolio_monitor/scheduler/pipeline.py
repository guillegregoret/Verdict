"""AlertPipeline: encadena señales → fundamentals → reasoning → notifier (§2).

Procesa dos fuentes de señal por ciclo:
- Movimientos de precio (TriggerEngine): compra en dip / tomar ganancias / consolidar.
- Deterioro de fundamentals (FundamentalsMonitor, §5.3): revisar la tesis.

Por cada señal: arma el contexto, genera la sugerencia, la manda por Telegram y
—solo si el envío fue exitoso— registra la alerta (activa el cooldown). Read-only.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine

from ..db.repositories import (
    AlertRepository,
    FundamentalsRepository,
    FundamentalsRow,
)
from ..fundamentals import FundamentalsEvent
from ..logging import get_logger
from ..notifier import NotifierError
from ..reasoning import ReasoningContext, ReasoningError, ReasoningService, Suggestion
from ..trigger import TriggerEngine, TriggerEvent

logger = get_logger(__name__)


# ── Protocolos para desacoplar de las clases concretas (testabilidad) ────────
class TriggerSource(Protocol):
    def evaluate(self) -> list[TriggerEvent]: ...


class DecaySource(Protocol):
    def evaluate(self) -> list[FundamentalsEvent]: ...


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
    """Orquesta la evaluación de señales hasta la notificación."""

    def __init__(
        self,
        trigger: TriggerSource,
        fundamentals: FundamentalsReader,
        reasoning: SuggestionMaker,
        notifier: MessageSender,
        alerts: AlertSink,
        fundamentals_monitor: DecaySource | None = None,
    ) -> None:
        self._trigger = trigger
        self._fundamentals = fundamentals
        self._reasoning = reasoning
        self._notifier = notifier
        self._alerts = alerts
        self._fundamentals_monitor = fundamentals_monitor

    @classmethod
    def from_engine(
        cls,
        engine: Engine,
        reasoning: ReasoningService,
        notifier: MessageSender,
        fundamentals: FundamentalsReader | None = None,
        fundamentals_monitor: DecaySource | None = None,
    ) -> AlertPipeline:
        """Cablea los repos y el trigger engine sobre `engine`.

        `fundamentals`: reader de fundamentals (None → repo read-only). `main.py`
        inyecta el FundamentalsService (fetch on-trigger) y el FundamentalsMonitor
        (deterioro, §5.3).
        """
        return cls(
            trigger=TriggerEngine.from_engine(engine),
            fundamentals=fundamentals or FundamentalsRepository(engine),
            reasoning=reasoning,
            notifier=notifier,
            alerts=AlertRepository(engine),
            fundamentals_monitor=fundamentals_monitor,
        )

    def run_once(self) -> int:
        """Un ciclo. Devuelve cuántas alertas se enviaron."""
        sent = 0

        price_events = self._trigger.evaluate()
        for event in price_events:
            context = ReasoningContext.from_trigger_event(
                event, fundamentals=self._safe_latest(event.ticker)
            )
            if self._dispatch(
                context,
                trigger_type=event.trigger_type,
                pct_change=event.pct_change,
                window_minutes=event.window_minutes,
            ):
                sent += 1

        decay_events = (
            self._fundamentals_monitor.evaluate()
            if self._fundamentals_monitor is not None
            else []
        )
        for decay in decay_events:
            context = ReasoningContext.from_fundamentals_event(decay)
            if self._dispatch(
                context, trigger_type=decay.trigger_type, pct_change=0.0, window_minutes=0
            ):
                sent += 1

        total = len(price_events) + len(decay_events)
        if total:
            logger.info("Pipeline: %d/%d alertas enviadas.", sent, total)
        return sent

    def _safe_latest(self, ticker: str) -> FundamentalsRow | None:
        """Fundamentals best-effort: un fallo no frena la alerta de precio."""
        try:
            return self._fundamentals.latest(ticker)
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(
                "Fundamentals %s no disponibles (%s); sigo sin ellos.", ticker, exc
            )
            return None

    def _dispatch(
        self,
        context: ReasoningContext,
        *,
        trigger_type: str,
        pct_change: float,
        window_minutes: int,
    ) -> bool:
        """Razona → notifica → registra. Devuelve True si se envió y registró."""
        try:
            suggestion = self._reasoning.suggest(context)
        except ReasoningError as exc:
            logger.warning("Reasoning falló para %s: %s", context.ticker, exc)
            return False

        try:
            self._notifier.send(suggestion.text)
        except NotifierError as exc:
            logger.warning("Notificación falló para %s: %s", context.ticker, exc)
            return False  # no registramos si no se envió (cooldown intacto)

        self._alerts.record(
            ticker=context.ticker,
            trigger_type=trigger_type,
            pct_change=pct_change,
            window_minutes=window_minutes,
            verdict=context.verdict,
            suggestion=suggestion.text,
        )
        return True
