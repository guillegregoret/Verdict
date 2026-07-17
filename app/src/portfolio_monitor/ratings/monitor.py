"""RatingsMonitor: alerta de cambio de consenso de analistas (§5).

Convierte el consenso (strongBuy…strongSell) a un score 1-5 y compara el actual
vs un baseline ≥ N días más viejo. Si el score se movió más que el umbral (mejoró
o se deterioró), emite una señal. Aplica a todos los holdings. Cooldown propio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..db.repositories import (
    AlertRepository,
    HoldingsRepository,
    LastAlert,
    RatingRow,
    RatingsRepository,
)
from ..logging import get_logger
from ..reasoning import MonitorSignal, ReasoningContext

logger = get_logger(__name__)

_TRIGGER_TYPE = "rating_shift"


@dataclass(frozen=True)
class RatingEvent:
    """Cambio material del consenso de analistas de un ticker."""

    ticker: str
    verdict: str
    note: str
    trigger_type: str = _TRIGGER_TYPE


class VerdictSource(Protocol):
    def verdicts_by_ticker(self) -> dict[str, str]: ...


class RatingsHistory(Protocol):
    def latest_and_baseline(
        self, ticker: str, min_gap_days: int
    ) -> tuple[RatingRow, RatingRow] | None: ...


class CooldownSource(Protocol):
    def last_alert(
        self, ticker: str, since: datetime, trigger_type: str | None = ...
    ) -> LastAlert | None: ...


def _score(row: RatingRow) -> tuple[float, int]:
    """Consenso a score 1-5 (5=strongBuy) + total de analistas."""
    total = row.strong_buy + row.buy + row.hold + row.sell + row.strong_sell
    if total == 0:
        return 0.0, 0
    weighted = (
        row.strong_buy * 5 + row.buy * 4 + row.hold * 3
        + row.sell * 2 + row.strong_sell * 1
    )
    return weighted / total, total


class RatingsMonitor:
    """Evalúa los holdings y emite señales de cambio de rating."""

    def __init__(
        self,
        verdicts: VerdictSource,
        ratings: RatingsHistory,
        alerts: CooldownSource,
        settings: Settings,
    ) -> None:
        self._verdicts = verdicts
        self._ratings = ratings
        self._alerts = alerts
        self._settings = settings

    @classmethod
    def from_engine(cls, engine: Engine, settings: Settings) -> RatingsMonitor:
        return cls(
            verdicts=HoldingsRepository(engine),
            ratings=RatingsRepository(engine),
            alerts=AlertRepository(engine),
            settings=settings,
        )

    def signals(self) -> list[MonitorSignal]:
        return [
            MonitorSignal(
                context=ReasoningContext.from_rating_event(event),
                trigger_type=event.trigger_type,
            )
            for event in self.evaluate()
        ]

    def evaluate(self, now: datetime | None = None) -> list[RatingEvent]:
        """Un ciclo. Devuelve los cambios de consenso nuevos (fuera de cooldown)."""
        now = now or datetime.now(UTC)
        s = self._settings
        cooldown_since = now - timedelta(days=s.ratings_cooldown_days)

        events: list[RatingEvent] = []
        for ticker, verdict in self._verdicts.verdicts_by_ticker().items():
            pair = self._ratings.latest_and_baseline(
                ticker, s.ratings_baseline_min_age_days
            )
            if pair is None:
                continue
            current, baseline = pair
            score_cur, n_cur = _score(current)
            score_base, _ = _score(baseline)
            if n_cur == 0:
                continue
            delta = score_cur - score_base
            if abs(delta) < s.ratings_shift_threshold:
                continue
            if self._alerts.last_alert(ticker, cooldown_since, _TRIGGER_TYPE) is not None:
                continue
            direction = "mejoró" if delta > 0 else "se deterioró"
            note = (
                f"consenso de analistas {direction}: {score_base:.1f}→{score_cur:.1f} "
                f"(escala 1-5, {n_cur} analistas)"
            )
            events.append(RatingEvent(ticker=ticker, verdict=verdict, note=note))
            logger.info("Rating shift %s: %s", ticker, note)
        return events
