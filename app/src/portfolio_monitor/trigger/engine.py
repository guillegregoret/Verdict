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
    PriceRepository,
    TickerConfig,
    TickerConfigRepository,
)
from ..logging import get_logger
from .verdict_gate import trigger_rule

logger = get_logger(__name__)


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


class VerdictSource(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...


class CooldownSource(Protocol):
    def alerted_tickers_since(self, since: datetime) -> set[str]: ...


class TriggerEngine:
    """Evalúa los tickers habilitados y emite eventos accionables."""

    def __init__(
        self,
        configs: ConfigSource,
        prices: PriceReader,
        verdicts: VerdictSource,
        alerts: CooldownSource,
    ) -> None:
        self._configs = configs
        self._prices = prices
        self._verdicts = verdicts
        self._alerts = alerts

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
        # Cooldown: una alerta por ticker por día (§5.5).
        day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        alerted_today = self._alerts.alerted_tickers_since(day_start)
        verdicts = self._verdicts.verdicts_by_ticker()

        events: list[TriggerEvent] = []
        for cfg in self._configs.enabled_configs():
            if cfg.ticker in alerted_today:
                continue  # ya alertado hoy

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
            magnitude = abs(cfg.threshold_pct)   # umbral en magnitud, ambos lados
            if rule.direction == "drop":
                if pct_change > -magnitude:
                    continue  # no cayó lo suficiente
                trigger_type = "drop_pct"
            else:  # "rise"
                if pct_change < magnitude:
                    continue  # no subió lo suficiente
                trigger_type = "rise_pct"

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
                "Trigger %s (%s): %.2f%% en %dm (umbral ±%.2f%%, veredicto %s).",
                cfg.ticker,
                rule.action,
                pct_change,
                cfg.window_minutes,
                magnitude,
                verdict,
            )

        return events
