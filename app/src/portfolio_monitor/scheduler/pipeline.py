"""AlertPipeline: encadena señales → fundamentals → reasoning → notifier (§2).

Procesa las fuentes de señal por ciclo:
- Movimientos de precio (TriggerEngine): compra en dip / tomar ganancias / consolidar.
- Monitores (§5): deterioro de fundamentals, cambios de rating, post-earnings, …
  Cada uno emite `MonitorSignal`s ya listos (contexto + tipo), con su propio cooldown.

Por cada señal: arma el contexto, genera la sugerencia, la manda por Telegram y
—solo si el envío fue exitoso— registra la alerta (activa el cooldown). Read-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from sqlalchemy import Engine

from ..db.repositories import (
    AlertRepository,
    FundamentalsRepository,
    FundamentalsRow,
    HoldingsRepository,
    PriceRepository,
)
from ..dca import DcaSuggestion
from ..logging import get_logger
from ..market import MarketContextService, MarketSnapshot
from ..notifier import NotifierError
from ..reasoning import (
    MonitorSignal,
    ReasoningContext,
    ReasoningError,
    ReasoningService,
    Suggestion,
)
from ..trigger import TriggerEngine, TriggerEvent

logger = get_logger(__name__)


# ── Protocolos para desacoplar de las clases concretas (testabilidad) ────────
class TriggerSource(Protocol):
    def evaluate(self) -> list[TriggerEvent]: ...


class MonitorLike(Protocol):
    def signals(self) -> list[MonitorSignal]: ...


class DcaSizerLike(Protocol):
    def size(self, ticker: str, pct_change: float) -> DcaSuggestion | None: ...


class FundamentalsReader(Protocol):
    def latest(self, ticker: str) -> FundamentalsRow | None: ...


class CostBasisReader(Protocol):
    def avg_cost_by_ticker(self) -> dict[str, float]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...


class MarketProvider(Protocol):
    def snapshot(self) -> MarketSnapshot | None: ...


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
        monitors: Sequence[MonitorLike] = (),
        dca: DcaSizerLike | None = None,
        holdings: CostBasisReader | None = None,
        prices: PriceReader | None = None,
        market: MarketProvider | None = None,
    ) -> None:
        self._trigger = trigger
        self._fundamentals = fundamentals
        self._reasoning = reasoning
        self._notifier = notifier
        self._alerts = alerts
        self._monitors = tuple(monitors)
        self._dca = dca
        self._holdings = holdings
        self._prices = prices
        self._market = market

    @classmethod
    def from_engine(
        cls,
        engine: Engine,
        reasoning: ReasoningService,
        notifier: MessageSender,
        fundamentals: FundamentalsReader | None = None,
        monitors: Sequence[MonitorLike] = (),
        dca: DcaSizerLike | None = None,
    ) -> AlertPipeline:
        """Cablea los repos y el trigger engine sobre `engine`.

        `fundamentals`: reader de fundamentals (None → repo read-only). `main.py`
        inyecta el FundamentalsService (fetch on-trigger), los monitores (§5) y el
        DcaSizer (§5.4).
        """
        return cls(
            trigger=TriggerEngine.from_engine(engine),
            fundamentals=fundamentals or FundamentalsRepository(engine),
            reasoning=reasoning,
            notifier=notifier,
            alerts=AlertRepository(engine),
            monitors=monitors,
            dca=dca,
            holdings=HoldingsRepository(engine),
            prices=PriceRepository(engine),
            market=MarketContextService.from_engine(engine),
        )

    def run_once(self) -> int:
        """Un ciclo. Devuelve cuántas alertas se enviaron."""
        sent = 0
        avg_costs = self._avg_costs()
        market = self._market_snapshot()

        price_events = self._trigger.evaluate()
        for event in price_events:
            dca = (
                self._dca.size(event.ticker, event.pct_change)
                if self._dca is not None and event.action == "comprar_dip"
                else None
            )
            context = ReasoningContext.from_trigger_event(
                event,
                fundamentals=self._safe_latest(event.ticker),
                bucket_remaining=dca.available_cash if dca else None,
                dca_suggested_usd=dca.amount_usd if dca else None,
                avg_cost=avg_costs.get(event.ticker),
                market=market,
            )
            if self._dispatch(
                context,
                trigger_type=event.trigger_type,
                pct_change=event.pct_change,
                window_minutes=event.window_minutes,
            ):
                sent += 1

        monitor_signals: list[MonitorSignal] = []
        for monitor in self._monitors:
            monitor_signals.extend(monitor.signals())
        for signal in monitor_signals:
            enriched = self._with_position(
                self._with_fundamentals(signal.context), avg_costs
            )
            if market is not None and enriched.market is None:
                enriched = replace(enriched, market=market)
            if self._dispatch(
                enriched,
                trigger_type=signal.trigger_type,
                pct_change=0.0,
                window_minutes=0,
            ):
                sent += 1

        total = len(price_events) + len(monitor_signals)
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

    def _with_fundamentals(self, context: ReasoningContext) -> ReasoningContext:
        """Adjunta fundamentals frescos a una señal de monitor para el análisis.

        Los monitores (post-earnings, rating, …) no cargan fundamentals: sin
        esto, el reasoner leería "no disponibles" justo cuando más importan
        (verificar la tesis en earnings / cambios de rating). Si la señal ya los
        trae (deterioro), se respeta. Best-effort: si faltan, sigue sin ellos.
        """
        if context.fundamentals is not None:
            return context
        return replace(context, fundamentals=self._safe_latest(context.ticker))

    def _avg_costs(self) -> dict[str, float]:
        """Costo promedio por ticker, best-effort (un fallo no frena las alertas)."""
        if self._holdings is None:
            return {}
        try:
            return self._holdings.avg_cost_by_ticker()
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning("Costos promedio no disponibles (%s); sigo sin ellos.", exc)
            return {}

    def _market_snapshot(self) -> MarketSnapshot | None:
        """Contexto de mercado del ciclo (una vez, compartido por todas las señales)."""
        if self._market is None:
            return None
        return self._market.snapshot()  # ya es best-effort (devuelve None si falla)

    def _with_position(
        self, context: ReasoningContext, avg_costs: dict[str, float]
    ) -> ReasoningContext:
        """Adjunta costo promedio (y precio actual) a una señal de monitor.

        Las señales de monitor no traen precio ni costo: sin esto el reasoner no
        puede decir si la posición viene en verde o en rojo, y termina sugiriendo
        "tomar ganancias" sobre una posición que está en pérdida.
        """
        price = context.current_price
        if price == 0 and self._prices is not None:
            try:
                price = self._prices.latest_price(context.ticker) or 0.0
            except Exception as exc:  # noqa: BLE001 - best-effort
                logger.warning("Precio %s no disponible (%s).", context.ticker, exc)
        return replace(
            context,
            current_price=price,
            avg_cost=avg_costs.get(context.ticker, context.avg_cost),
        )

    def _dispatch(
        self,
        context: ReasoningContext,
        *,
        trigger_type: str,
        pct_change: float,
        window_minutes: int,
    ) -> bool:
        """Razona → notifica → registra. Devuelve True si se envió y registró.

        Antepone `[TICKER]` al mensaje (el texto del reasoner no siempre nombra el
        activo al principio) para que se lea de un vistazo de qué stock se trata.
        """
        try:
            suggestion = self._reasoning.suggest(context)
        except ReasoningError as exc:
            logger.warning("Reasoning falló para %s: %s", context.ticker, exc)
            return False

        message = f"[{context.ticker}] {suggestion.text}"
        try:
            self._notifier.send(message)
        except NotifierError as exc:
            logger.warning("Notificación falló para %s: %s", context.ticker, exc)
            return False  # no registramos si no se envió (cooldown intacto)

        self._alerts.record(
            ticker=context.ticker,
            trigger_type=trigger_type,
            pct_change=pct_change,
            window_minutes=window_minutes,
            verdict=context.verdict,
            suggestion=message,
        )
        return True
