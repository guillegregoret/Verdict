"""Digests semanales (§5).

- `EarningsDigest`: lista los earnings de la semana de tus holdings (aviso del lunes).
- `PortfolioDigest`: resumen de cómo varió el portfolio en la semana (aviso del viernes).
- `WeeklyDigestRunner`: lo corre el scheduler cada tick; dispara según la hora de
  mercado (New York) y deduplica por día con el digest_log.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from ..config import Settings
from ..db.repositories import (
    DigestLogRepository,
    EarningsRepository,
    HoldingsRepository,
    PriceRepository,
    UpcomingEarnings,
)
from ..logging import get_logger
from ..notifier import NotifierError

logger = get_logger(__name__)

_MARKET_TZ = ZoneInfo("America/New_York")
_HOUR_LABEL = {
    "bmo": "pre-apertura",
    "amc": "post-cierre",
    "dmh": "en rueda",
    "": "",
}


# ── Protocolos (testabilidad) ────────────────────────────────────────────────
class EarningsReader(Protocol):
    def upcoming(self, start: date, end: date) -> list[UpcomingEarnings]: ...


class SharesReader(Protocol):
    def shares_by_ticker(self) -> dict[str, float]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...
    def price_at_or_before(self, ticker: str, ts: datetime) -> float | None: ...


class MessageSender(Protocol):
    def send(self, text: str) -> None: ...


class DigestLog(Protocol):
    def was_sent(self, kind: str, day: date) -> bool: ...
    def mark_sent(self, kind: str, day: date) -> None: ...


# ── Digests ──────────────────────────────────────────────────────────────────
class EarningsDigest:
    """Aviso de los lunes: earnings de la semana (lun-dom) de los holdings."""

    def __init__(self, earnings: EarningsReader) -> None:
        self._earnings = earnings

    def build(self, now: datetime) -> str:
        monday = now.date() - timedelta(days=now.weekday())
        rows = self._earnings.upcoming(monday, monday + timedelta(days=6))
        if not rows:
            return "📅 Earnings de la semana: ninguno de tus holdings reporta esta semana."
        lines = ["📅 Earnings de tus holdings esta semana:"]
        for r in rows:
            hour = _HOUR_LABEL.get(r.hour, r.hour)
            hour_s = f" · {hour}" if hour else ""
            est = f" · EPS est {r.eps_estimate:.2f}" if r.eps_estimate is not None else ""
            verdict = f" [{r.verdict}]" if r.verdict else ""
            lines.append(
                f"• {r.earnings_date:%a %d/%m} — {r.ticker}{verdict}{hour_s}{est}"
            )
        return "\n".join(lines)


class PortfolioDigest:
    """Aviso de los viernes: variación del portfolio en la semana."""

    def __init__(self, holdings: SharesReader, prices: PriceReader) -> None:
        self._holdings = holdings
        self._prices = prices

    def build(self, now: datetime) -> str | None:
        shares = self._holdings.shares_by_ticker()
        if not shares:
            return None
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        total_now = 0.0
        total_prev = 0.0
        moves: list[tuple[str, float]] = []
        for ticker, sh in shares.items():
            cur = self._prices.latest_price(ticker)
            if cur is None:
                continue
            total_now += sh * cur
            prev = self._prices.price_at_or_before(ticker, week_start)
            if prev:
                total_prev += sh * prev
                moves.append((ticker, (cur - prev) / prev * 100))

        if total_prev == 0:
            return None
        var_pct = (total_now - total_prev) / total_prev * 100
        arrow = "📈" if var_pct >= 0 else "📉"
        lines = [
            f"{arrow} Resumen semanal del portfolio",
            f"Valor: ${total_now:,.0f}  ({var_pct:+.2f}% en la semana)",
        ]
        if moves:
            moves.sort(key=lambda m: m[1], reverse=True)
            best = ", ".join(f"{t} {p:+.1f}%" for t, p in moves[:3])
            worst = ", ".join(f"{t} {p:+.1f}%" for t, p in reversed(moves[-3:]))
            lines.append(f"Mejores: {best}")
            lines.append(f"Peores: {worst}")
        return "\n".join(lines)


# ── Runner (time-gate + dedupe) ──────────────────────────────────────────────
class WeeklyDigestRunner:
    """Corre los digests según la hora de mercado; deduplica por día (§5)."""

    def __init__(
        self,
        earnings_digest: EarningsDigest,
        portfolio_digest: PortfolioDigest,
        notifier: MessageSender,
        log: DigestLog,
        settings: Settings,
    ) -> None:
        self._earnings_digest = earnings_digest
        self._portfolio_digest = portfolio_digest
        self._notifier = notifier
        self._log = log
        self._settings = settings

    @classmethod
    def from_engine(
        cls, engine: Engine, notifier: MessageSender, settings: Settings
    ) -> WeeklyDigestRunner:
        return cls(
            earnings_digest=EarningsDigest(EarningsRepository(engine)),
            portfolio_digest=PortfolioDigest(
                HoldingsRepository(engine), PriceRepository(engine)
            ),
            notifier=notifier,
            log=DigestLogRepository(engine),
            settings=settings,
        )

    def run(self, now: datetime | None = None) -> None:
        et = (now or datetime.now(UTC)).astimezone(_MARKET_TZ)
        s = self._settings
        if et.weekday() == 0 and et.hour >= s.digest_monday_hour_et:
            self._fire("monday_earnings", et, self._earnings_digest.build(et))
        elif et.weekday() == 4 and et.hour >= s.digest_friday_hour_et:
            self._fire("friday_summary", et, self._portfolio_digest.build(et))

    def _fire(self, kind: str, et: datetime, text: str | None) -> None:
        day = et.date()
        if text is None or self._log.was_sent(kind, day):
            return
        try:
            self._notifier.send(text)
        except NotifierError as exc:
            logger.warning("Digest %s falló al enviar: %s", kind, exc)
            return  # reintenta el próximo tick (no marca enviado)
        self._log.mark_sent(kind, day)
        logger.info("Digest %s enviado.", kind)
