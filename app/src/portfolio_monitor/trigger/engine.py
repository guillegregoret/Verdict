"""Trigger Engine (CLAUDE.md §5): detección de movimientos + Verdict Gate + cooldown.

Read-only sobre la DB. `evaluate()` devuelve los `TriggerEvent` accionables; los
módulos de reasoning (§6) y notifier (§7) los consumen y recién ahí se persiste la
alerta. No llama a Anthropic ni Telegram.

Según el veredicto (Verdict Gate, §4), un ticker se evalúa por **caída** (compra
en el dip: Crecer/Mantener) o por **suba** (tomar ganancias/consolidar: Trim/
Consolidar). El umbral por-ticker (`threshold_pct`) se toma en magnitud para
ambas direcciones.

Pendiente para fases siguientes: chequeo de fundamentals (§5.3), bucket awareness
(§5.4) y el reset del cooldown cuando el precio revierte sobre el umbral (§5.5 —
hoy es 1/día a secas, conservador contra fatiga de alertas).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..db.repositories import (
    AlertRepository,
    HoldingsRepository,
    LastAlert,
    PriceRepository,
    TickerConfig,
    TickerConfigRepository,
)
from ..logging import get_logger
from .verdict_gate import trigger_rule

logger = get_logger(__name__)

# Fracción del movimiento que el precio debe retroceder para re-armar el cooldown
# (§5.5): 0.5 = el precio revierte a mitad de camino del pico/piso hacia la
# referencia previa. Ej: cayó -6% → re-arma si recupera a -3% de la referencia.
_RECOVERY_FRACTION = 0.5


@dataclass(frozen=True)
class TriggerEvent:
    """Un movimiento detectado que pasó el Verdict Gate y el cooldown."""

    ticker: str
    pct_change: float          # negativo si cayó (-5.2), positivo si subió (+6.1)
    window_minutes: int
    reference_price: float
    current_price: float
    verdict: str
    trigger_type: str = "drop_pct"   # "drop_pct" | "rise_pct"
    action: str = "comprar_dip"      # "comprar_dip" | "tomar_ganancias" | "consolidar"


# ── Protocolos para desacoplar de los repos concretos (testabilidad) ─────────
class ConfigSource(Protocol):
    def enabled_configs(self) -> list[TickerConfig]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...
    def reference_price(self, ticker: str, since: datetime) -> float | None: ...
    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None: ...
    def max_price_since(self, ticker: str, since: datetime) -> float | None: ...
    def min_price_since(self, ticker: str, since: datetime) -> float | None: ...


class VerdictSource(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...


class CooldownSource(Protocol):
    def last_alert(self, ticker: str, since: datetime) -> LastAlert | None: ...


class TriggerEngine:
    """Evalúa los tickers habilitados y emite eventos accionables."""

    def __init__(
        self,
        configs: ConfigSource,
        prices: PriceReader,
        verdicts: VerdictSource,
        alerts: CooldownSource,
        cooldown_hours: int = 24,
    ) -> None:
        self._configs = configs
        self._prices = prices
        self._verdicts = verdicts
        self._alerts = alerts
        self._cooldown_hours = cooldown_hours

    @classmethod
    def from_engine(cls, engine: Engine) -> TriggerEngine:
        """Construye el engine cableando los repos concretos sobre `engine`."""
        return cls(
            configs=TickerConfigRepository(engine),
            prices=PriceRepository(engine),
            verdicts=HoldingsRepository(engine),
            alerts=AlertRepository(engine),
        )

    def evaluate(self, now: datetime | None = None) -> list[TriggerEvent]:
        """Un ciclo de evaluación. Devuelve los eventos accionables detectados."""
        now = now or datetime.now(UTC)
        verdicts = self._verdicts.verdicts_by_ticker()

        events: list[TriggerEvent] = []
        for cfg in self._configs.enabled_configs():
            verdict = verdicts.get(cfg.ticker)
            rule = trigger_rule(verdict)
            if rule is None:
                continue  # Verdict Gate: veredicto no accionable

            window_start = now - timedelta(minutes=cfg.window_minutes)
            reference = self._prices.reference_price(cfg.ticker, window_start)
            current = self._prices.latest_price(cfg.ticker)
            if reference is None or current is None or reference == 0:
                continue  # datos insuficientes en la ventana

            pct_change = (current - reference) / reference * 100.0
            if rule.direction == "drop":
                threshold = cfg.threshold_pct        # negativo (ej: -4.5)
                if pct_change > threshold:
                    continue  # no cayó lo suficiente
                trigger_type = "drop_pct"
            else:  # "rise"
                threshold = cfg.rise_threshold_pct   # positivo (ej: +8.0)
                if pct_change < threshold:
                    continue  # no subió lo suficiente
                trigger_type = "rise_pct"

            # Cooldown (§5.5): supprime si ya alertó y el precio NO revirtió aún.
            if self._on_cooldown(cfg.ticker, now):
                continue

            assert verdict is not None  # trigger_rule() ya lo garantizó
            events.append(
                TriggerEvent(
                    ticker=cfg.ticker,
                    pct_change=pct_change,
                    window_minutes=cfg.window_minutes,
                    reference_price=reference,
                    current_price=current,
                    verdict=verdict,
                    trigger_type=trigger_type,
                    action=rule.action,
                )
            )
            logger.info(
                "Trigger %s (%s): %.2f%% en %dm (umbral %.2f%%, veredicto %s).",
                cfg.ticker,
                rule.action,
                pct_change,
                cfg.window_minutes,
                threshold,
                verdict,
            )

        return events

    def _on_cooldown(self, ticker: str, now: datetime) -> bool:
        """True si el ticker ya alertó dentro de la ventana y aún NO revirtió (§5.5).

        Sin alerta reciente → libre. Con alerta reciente, se re-arma solo si el
        precio retrocedió `_RECOVERY_FRACTION` del movimiento hacia la referencia
        previa (así un segundo dip/rally genuino vuelve a avisar, sin spamear el mismo).
        """
        since = now - timedelta(hours=self._cooldown_hours)
        last = self._alerts.last_alert(ticker, since)
        if last is None:
            return False
        return not self._reverted_since(ticker, last)

    def _reverted_since(self, ticker: str, last: LastAlert) -> bool:
        """¿El precio revirtió lo suficiente desde la última alerta para re-armar?"""
        alert_price = self._prices.price_at_or_before(ticker, last.ts)
        if alert_price is None or (1 + last.pct_change / 100) == 0:
            return False  # no podemos evaluar → conservador (sigue en cooldown)

        # Referencia pre-movimiento reconstruida desde el pct_change guardado.
        pre = alert_price / (1 + last.pct_change / 100)
        recover_level = pre * (1 + last.pct_change * (1 - _RECOVERY_FRACTION) / 100)

        if last.trigger_type == "drop_pct":
            peak = self._prices.max_price_since(ticker, last.ts)
            return peak is not None and peak >= recover_level
        # rise_pct: revierte si retrocede hacia abajo
        trough = self._prices.min_price_since(ticker, last.ts)
        return trough is not None and trough <= recover_level
