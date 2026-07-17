"""PostEarningsMonitor: reacción post-earnings (§5).

Cuando un holding reporta (aparece eps_actual), avisa con la sorpresa (actual vs
estimado) y cómo reaccionó el precio desde el reporte, para leerlo según el
veredicto. Cooldown propio para no repetir el mismo reporte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..db.repositories import (
    AlertRepository,
    EarningsRepository,
    LastAlert,
    PriceRepository,
    ReportedEarnings,
)
from ..logging import get_logger
from ..reasoning import MonitorSignal, ReasoningContext

logger = get_logger(__name__)

_TRIGGER_TYPE = "post_earnings"


@dataclass(frozen=True)
class PostEarningsEvent:
    """Un holding que reportó earnings recientemente."""

    ticker: str
    verdict: str
    note: str
    trigger_type: str = _TRIGGER_TYPE


class ReportedSource(Protocol):
    def reported_since(self, start) -> list[ReportedEarnings]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...
    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None: ...


class CooldownSource(Protocol):
    def last_alert(
        self, ticker: str, since: datetime, trigger_type: str | None = ...
    ) -> LastAlert | None: ...


class PostEarningsMonitor:
    """Emite señales de reacción post-earnings de los holdings."""

    def __init__(
        self,
        earnings: ReportedSource,
        prices: PriceReader,
        alerts: CooldownSource,
        settings: Settings,
    ) -> None:
        self._earnings = earnings
        self._prices = prices
        self._alerts = alerts
        self._settings = settings

    @classmethod
    def from_engine(cls, engine: Engine, settings: Settings) -> PostEarningsMonitor:
        return cls(
            earnings=EarningsRepository(engine),
            prices=PriceRepository(engine),
            alerts=AlertRepository(engine),
            settings=settings,
        )

    def signals(self) -> list[MonitorSignal]:
        return [
            MonitorSignal(
                context=ReasoningContext.from_postearnings_event(event),
                trigger_type=event.trigger_type,
            )
            for event in self.evaluate()
        ]

    def evaluate(self, now: datetime | None = None) -> list[PostEarningsEvent]:
        """Un ciclo. Devuelve los reportes nuevos (fuera de cooldown)."""
        now = now or datetime.now(UTC)
        s = self._settings
        start = now.date() - timedelta(days=s.postearnings_lookback_days)
        cooldown_since = now - timedelta(days=s.postearnings_cooldown_days)

        events: list[PostEarningsEvent] = []
        for r in self._earnings.reported_since(start):
            if r.verdict is None or r.eps_actual is None:
                continue  # solo holdings, y solo si ya reportó
            if self._alerts.last_alert(r.ticker, cooldown_since, _TRIGGER_TYPE) is not None:
                continue

            events.append(
                PostEarningsEvent(
                    ticker=r.ticker, verdict=r.verdict, note=self._note(r, now)
                )
            )
            logger.info("Post-earnings %s: reportó %s", r.ticker, r.earnings_date)
        return events

    def _note(self, r: ReportedEarnings, now: datetime) -> str:
        """Sorpresa de EPS (si es plausible) + reacción del precio desde el reporte."""
        parts: list[str] = []
        if r.eps_estimate not in (None, 0) and r.eps_actual is not None:
            surprise = (r.eps_actual - r.eps_estimate) / abs(r.eps_estimate) * 100
            # Guard: sorpresas absurdas (>150%) suelen ser quirks de unidades de
            # listings extranjeros (ej: TSM en TWD) → omitimos el EPS, queda el precio.
            if abs(surprise) <= 150:
                parts.append(
                    f"EPS {r.eps_actual:.2f} vs est {r.eps_estimate:.2f} "
                    f"(sorpresa {surprise:+.0f}%)"
                )

        pre_ts = datetime.combine(r.earnings_date, time.min, tzinfo=UTC)
        before = self._prices.price_at_or_before(r.ticker, pre_ts)
        current = self._prices.latest_price(r.ticker)
        if before and current:
            react_pct = (current - before) / before * 100
            parts.append(f"precio {react_pct:+.1f}% desde el reporte")

        detail = " · ".join(parts) if parts else "reporte disponible"
        return f"reportó ({r.earnings_date:%d/%m}): {detail}"
