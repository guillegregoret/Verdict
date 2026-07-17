"""FundamentalsMonitor: alerta de deterioro de la tesis (§5.3).

Compara el último snapshot de fundamentals de cada nombre que tenés contra un
baseline ≥ N días más viejo (captura el cambio trimestral) y avisa si empeoró
materialmente — sin importar el precio. Es la señal que el precio solo no da:
"la tesis se está deteriorando, revisá".

Aplica a TODOS los holdings (no solo Crecer/Mantener): querés enterarte si
cualquier posición se degrada. Cooldown propio (fundamentals_decay) para no
repetir el mismo deterioro.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..db.repositories import (
    AlertRepository,
    FundamentalsRepository,
    FundamentalsRow,
    HoldingsRepository,
    LastAlert,
)
from ..logging import get_logger
from ..reasoning import MonitorSignal, ReasoningContext

logger = get_logger(__name__)

_TRIGGER_TYPE = "fundamentals_decay"


@dataclass(frozen=True)
class FundamentalsEvent:
    """Deterioro detectado en la tesis de un ticker."""

    ticker: str
    verdict: str
    reasons: tuple[str, ...]      # descripciones legibles de lo que empeoró
    current: FundamentalsRow
    baseline: FundamentalsRow
    trigger_type: str = _TRIGGER_TYPE


# ── Protocolos (testabilidad) ────────────────────────────────────────────────
class VerdictSource(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...


class FundamentalsHistory(Protocol):
    def latest_and_baseline(
        self, ticker: str, min_gap_days: int
    ) -> tuple[FundamentalsRow, FundamentalsRow] | None: ...


class CooldownSource(Protocol):
    def last_alert(
        self, ticker: str, since: datetime, trigger_type: str | None = ...
    ) -> LastAlert | None: ...


class FundamentalsMonitor:
    """Evalúa los holdings y emite eventos de deterioro de fundamentals."""

    def __init__(
        self,
        verdicts: VerdictSource,
        fundamentals: FundamentalsHistory,
        alerts: CooldownSource,
        settings: Settings,
    ) -> None:
        self._verdicts = verdicts
        self._fundamentals = fundamentals
        self._alerts = alerts
        self._settings = settings

    @classmethod
    def from_engine(cls, engine: Engine, settings: Settings) -> FundamentalsMonitor:
        return cls(
            verdicts=HoldingsRepository(engine),
            fundamentals=FundamentalsRepository(engine),
            alerts=AlertRepository(engine),
            settings=settings,
        )

    def signals(self) -> list[MonitorSignal]:
        """Deterioros como MonitorSignal listos para el pipeline (§5.3)."""
        return [
            MonitorSignal(
                context=ReasoningContext.from_fundamentals_event(event),
                trigger_type=event.trigger_type,
            )
            for event in self.evaluate()
        ]

    def evaluate(self, now: datetime | None = None) -> list[FundamentalsEvent]:
        """Un ciclo. Devuelve los deterioros nuevos (fuera de cooldown)."""
        now = now or datetime.now(UTC)
        s = self._settings
        cooldown_since = now - timedelta(days=s.fundamentals_decay_cooldown_days)

        events: list[FundamentalsEvent] = []
        for ticker, verdict in self._verdicts.verdicts_by_ticker().items():
            pair = self._fundamentals.latest_and_baseline(
                ticker, s.fundamentals_baseline_min_age_days
            )
            if pair is None:
                continue  # sin historial suficiente para comparar
            current, baseline = pair
            reasons = self._deterioration(current, baseline)
            if not reasons:
                continue
            if self._alerts.last_alert(ticker, cooldown_since, _TRIGGER_TYPE) is not None:
                continue  # ya avisamos este deterioro hace poco
            events.append(
                FundamentalsEvent(
                    ticker=ticker,
                    verdict=verdict,
                    reasons=tuple(reasons),
                    current=current,
                    baseline=baseline,
                )
            )
            logger.info("Deterioro de fundamentals %s: %s", ticker, "; ".join(reasons))
        return events

    def _deterioration(
        self, cur: FundamentalsRow, base: FundamentalsRow
    ) -> list[str]:
        """Reglas de deterioro. Devuelve descripciones legibles (vacío = sano)."""
        s = self._settings
        reasons: list[str] = []

        if cur.revenue_growth is not None and base.revenue_growth is not None:
            drop_pp = (base.revenue_growth - cur.revenue_growth) * 100
            if drop_pp >= s.fund_revenue_growth_drop_pp:
                reasons.append(
                    f"crecimiento de ingresos {base.revenue_growth * 100:+.1f}% → "
                    f"{cur.revenue_growth * 100:+.1f}%"
                )

        if cur.gross_margin is not None and base.gross_margin is not None:
            drop_pp = (base.gross_margin - cur.gross_margin) * 100
            if drop_pp >= s.fund_margin_drop_pp:
                reasons.append(
                    f"margen bruto {base.gross_margin * 100:.1f}% → "
                    f"{cur.gross_margin * 100:.1f}%"
                )

        if cur.debt_to_equity is not None and base.debt_to_equity is not None:
            rise = cur.debt_to_equity - base.debt_to_equity
            if rise >= s.fund_debt_rise:
                reasons.append(
                    f"deuda/equity {base.debt_to_equity:.2f} → {cur.debt_to_equity:.2f}"
                )

        return reasons
