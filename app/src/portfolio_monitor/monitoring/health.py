"""HealthService: estado de los componentes para el comando /health (§9).

Read-only: reporta si la DB responde, el último estado de Finnhub y la frescura
de cada fuente (último precio, último sync de posiciones/cash, últimos
fundamentals), más el uptime del proceso. No abre conexiones nuevas al gateway ni
necesita el Docker API — todo sale de la DB y del proceso.

El uptime de OTROS containers (gateway, postgres) y el /reconnect requieren acceso
al Docker API; se resuelven aparte (ver DockerControl) para no darle al monolito
más superficie de la necesaria por defecto (§8/§12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from ..logging import get_logger

logger = get_logger(__name__)

# Marca de arranque del proceso: el módulo se importa al iniciar la app, así que
# aproxima el uptime sin leer /proc. Inyectable en tests.
_PROCESS_START = datetime.now(UTC)

# Umbrales de frescura (segundos) para pintar 🟢/🟡. Precio: el poller barre cada
# ~90s. IBKR: sync cada ~30 min. Se es generoso para no alarmar fuera de rueda.
_PRICE_STALE_S = 15 * 60
_IBKR_STALE_S = 60 * 60
_FUND_STALE_S = 48 * 3600


@dataclass(frozen=True)
class Component:
    """Estado de un componente para el reporte."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HealthReport:
    """Foto de salud de los componentes en un instante."""

    now: datetime
    uptime_seconds: float
    db_ok: bool
    finnhub_status: str | None          # 'up' | 'degraded' | 'down' | None
    last_price_ts: datetime | None
    last_holdings_sync: datetime | None
    last_cash_sync: datetime | None
    last_fundamentals_ts: datetime | None
    containers: tuple[Component, ...] = ()  # uptimes de otros containers (opcional)


def _age_s(now: datetime, ts: datetime | None) -> float | None:
    if ts is None:
        return None
    return (now - ts).total_seconds()


def _fmt_age(seconds: float | None) -> str:
    """'hace 3m', 'hace 2h 5m', o 'sin datos'."""
    if seconds is None:
        return "sin datos"
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"hace {seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"hace {m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"hace {h}h {m}m"
    d, h = divmod(h, 24)
    return f"hace {d}d {h}h"


def _fmt_uptime(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _dot(ok: bool, stale: bool = False) -> str:
    if not ok:
        return "🔴"
    return "🟡" if stale else "🟢"


def format_health(r: HealthReport) -> str:
    """Renderiza el reporte de /health a texto para Telegram (pure)."""
    price_age = _age_s(r.now, r.last_price_ts)
    ibkr_age = _age_s(r.now, r.last_holdings_sync)
    cash_age = _age_s(r.now, r.last_cash_sync)
    fund_age = _age_s(r.now, r.last_fundamentals_ts)

    # 'ok' = Finnhub no reporta caída (down/degraded → 🔴). 'stale' = sin precios
    # nuevos hace rato (puede ser rueda cerrada → 🟡, no 🔴).
    finnhub_ok = r.finnhub_status in (None, "up")
    finnhub_stale = price_age is not None and price_age > _PRICE_STALE_S
    ibkr_ok = ibkr_age is not None
    ibkr_stale = ibkr_age is not None and ibkr_age > _IBKR_STALE_S
    fund_stale = fund_age is not None and fund_age > _FUND_STALE_S

    lines = [
        "🩺 Estado de Verdict",
        f"{_dot(r.db_ok)} Base de datos — {'responde' if r.db_ok else 'sin respuesta'}",
        f"{_dot(finnhub_ok, finnhub_stale)} Finnhub — "
        f"{r.finnhub_status or 'sin registro'} · último precio {_fmt_age(price_age)}",
        f"{_dot(ibkr_ok, ibkr_stale)} IBKR Gateway — posiciones {_fmt_age(ibkr_age)}"
        f" · cash {_fmt_age(cash_age)}",
        f"{_dot(True, fund_stale)} Fundamentals — {_fmt_age(fund_age)}",
    ]
    for c in r.containers:
        lines.append(f"{_dot(c.ok)} {c.name} — {c.detail}")
    lines.append(f"⏱️ Uptime app: {_fmt_uptime(r.uptime_seconds)}")
    if ibkr_stale or not ibkr_ok:
        lines.append(
            "\n⚠️ Las posiciones/cash están viejas: el gateway puede necesitar "
            "re-login. Probá /reconnect."
        )
    return "\n".join(lines)


class HealthService:
    """Arma el HealthReport consultando la DB (read-only) + uptime del proceso."""

    def __init__(self, engine: Engine, started_at: datetime | None = None) -> None:
        self._engine = engine
        self._started_at = started_at or _PROCESS_START

    def report(self, now: datetime | None = None) -> HealthReport:
        now = now or datetime.now(UTC)
        db_ok = True
        finnhub = last_price = last_hold = last_cash = last_fund = None
        try:
            with self._engine.connect() as conn:
                finnhub = conn.execute(
                    text(
                        "SELECT status FROM data_source_health WHERE source='finnhub' "
                        "ORDER BY ts DESC LIMIT 1"
                    )
                ).scalar_one_or_none()
                last_price = conn.execute(
                    text("SELECT max(ts) FROM prices")
                ).scalar_one_or_none()
                last_hold = conn.execute(
                    text("SELECT max(updated_at) FROM holdings WHERE shares IS NOT NULL")
                ).scalar_one_or_none()
                last_cash = conn.execute(
                    text("SELECT max(ts) FROM account_cash")
                ).scalar_one_or_none()
                last_fund = conn.execute(
                    text("SELECT max(ts) FROM fundamentals")
                ).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 - la DB no responde → db_ok=False
            logger.warning("Health: la DB no respondió (%s).", exc)
            db_ok = False

        return HealthReport(
            now=now,
            uptime_seconds=(now - self._started_at).total_seconds(),
            db_ok=db_ok,
            finnhub_status=finnhub,
            last_price_ts=last_price,
            last_holdings_sync=last_hold,
            last_cash_sync=last_cash,
            last_fundamentals_ts=last_fund,
        )

    def render(self, now: datetime | None = None) -> str:
        return format_health(self.report(now))
