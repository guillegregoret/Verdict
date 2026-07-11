"""Trigger Engine (CLAUDE.md §5): detección de caídas + Verdict Gate + cooldown.

Read-only sobre la DB. `evaluate()` devuelve los `TriggerEvent` accionables; los
módulos de reasoning (§6) y notifier (§7) los consumen y recién ahí se persiste la
alerta. No llama a Anthropic ni Telegram.

Pendiente para fases siguientes: chequeo de fundamentals (§5.3), bucket awareness
(§5.4) y alertas informativas para veredictos no-compra (§5.2).
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
from .verdict_gate import allows_buy

logger = get_logger(__name__)

_TRIGGER_TYPE = "drop_pct"


@dataclass(frozen=True)
class TriggerEvent:
    """Una caída detectada que pasó el Verdict Gate y el cooldown."""

    ticker: str
    pct_change: float          # negativo (ej: -5.2)
    window_minutes: int
    reference_price: float
    current_price: float
    verdict: str
    trigger_type: str = _TRIGGER_TYPE


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
            if not allows_buy(verdict):
                continue  # Verdict Gate: solo Crecer/Mantener

            window_start = now - timedelta(minutes=cfg.window_minutes)
            reference = self._prices.reference_price(cfg.ticker, window_start)
            current = self._prices.latest_price(cfg.ticker)
            if reference is None or current is None or reference == 0:
                continue  # datos insuficientes en la ventana

            pct_change = (current - reference) / reference * 100.0
            if pct_change > cfg.threshold_pct:
                continue  # no cayó lo suficiente (umbral es negativo)

            assert verdict is not None  # allows_buy() ya lo garantizó
            events.append(
                TriggerEvent(
                    ticker=cfg.ticker,
                    pct_change=pct_change,
                    window_minutes=cfg.window_minutes,
                    reference_price=reference,
                    current_price=current,
                    verdict=verdict,
                )
            )
            logger.info(
                "Trigger %s: %.2f%% en %dm (umbral %.2f%%, veredicto %s).",
                cfg.ticker,
                pct_change,
                cfg.window_minutes,
                cfg.threshold_pct,
                verdict,
            )

        return events
