"""Cliente Finnhub: quotes (backbone de precios) + fundamentals (§3, §5.3).

`FinnhubClient` cubre las quotes del price poller. `FinnhubFundamentalsProvider`
implementa el protocolo de fundamentals (§5.3) sobre `/stock/metric`, reutilizando
la key que ya usamos para precios. Free tier ~60 req/min: el spacing lo maneja el
poller; los fundamentals se traen solo on-trigger (pocos), así que no molesta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from ..config import Settings
from ..logging import get_logger

# Tipos compartidos de fundamentals (la abstracción vive en edgar_fmp por historia).
from .edgar_fmp import Fundamentals, FundamentalsError

logger = get_logger(__name__)


@dataclass(frozen=True)
class Quote:
    """Quote normalizada de Finnhub (`/quote`)."""

    ticker: str
    price: float          # `c` = current price
    ts: datetime          # `t` = unix timestamp (UTC)


class FinnhubError(RuntimeError):
    """Error al hablar con la API de Finnhub."""


class FinnhubClient:
    """Wrapper delgado sobre la API REST de Finnhub."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.finnhub_api_key:
            raise FinnhubError("FINNHUB_API_KEY no configurada.")
        self._api_key = settings.finnhub_api_key
        self._client = client or httpx.Client(
            base_url=settings.finnhub_base_url,
            timeout=httpx.Timeout(10.0),
        )

    def get_quote(self, ticker: str) -> Quote:
        """Trae la quote actual de un ticker.

        Lanza FinnhubError si la respuesta no trae precio válido (`c` == 0 y
        `t` == 0 suele indicar símbolo desconocido en el free tier).
        """
        try:
            resp = self._client.get(
                "/quote", params={"symbol": ticker, "token": self._api_key}
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:  # timeouts, 429, 5xx, red
            raise FinnhubError(f"Fallo consultando quote de {ticker}: {exc}") from exc

        price = data.get("c")
        raw_ts = data.get("t")
        if not price or not raw_ts:
            raise FinnhubError(f"Quote vacía/ inválida para {ticker}: {data!r}")

        return Quote(
            ticker=ticker,
            price=float(price),
            ts=datetime.fromtimestamp(int(raw_ts), tz=UTC),
        )

    def close(self) -> None:
        """Cierra el cliente HTTP subyacente."""
        self._client.close()

    def __enter__(self) -> FinnhubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _num(value: Any) -> float | None:
    """Coerciona a float; None si es None o no numérico."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_to_fraction(value: Any) -> float | None:
    """Finnhub da márgenes/crecimiento en % (74.15); los normaliza a fracción (0.7415)."""
    n = _num(value)
    return n / 100.0 if n is not None else None


class FinnhubFundamentalsProvider:
    """Provider de fundamentals sobre Finnhub `/stock/metric?metric=all` (§5.3).

    Cumple el protocolo `FundamentalsProvider`. Finnhub devuelve márgenes y
    crecimiento como PORCENTAJE (74.15 = 74.15%); se pasan a fracción para igualar
    la convención de FMP y del reasoner (que formatea ×100). P/E y deuda/equity ya
    son ratios y van tal cual. Se persiste el `metric` crudo en `raw`.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.finnhub_api_key:
            raise FundamentalsError("FINNHUB_API_KEY no configurada.")
        self._api_key = settings.finnhub_api_key
        self._client = client or httpx.Client(
            base_url=settings.finnhub_base_url,
            timeout=httpx.Timeout(15.0),
        )

    def fetch(self, ticker: str) -> Fundamentals | None:
        """Trae y normaliza los fundamentals de un ticker (None si no hay métricas)."""
        try:
            resp = self._client.get(
                "/stock/metric",
                params={"symbol": ticker, "metric": "all", "token": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise FundamentalsError(
                f"Fallo consultando Finnhub metric de {ticker}: {exc}"
            ) from exc

        metric = data.get("metric") if isinstance(data, dict) else None
        if not metric:
            logger.info("Finnhub sin métricas para %s.", ticker)
            return None

        return Fundamentals(
            ticker=ticker,
            pe=_num(metric.get("peTTM")),
            revenue_growth=_pct_to_fraction(metric.get("revenueGrowthTTMYoy")),
            gross_margin=_pct_to_fraction(metric.get("grossMarginTTM")),
            debt_to_equity=_num(metric.get("totalDebt/totalEquityQuarterly")),
            raw={"metric": metric},
            source="finnhub",
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FinnhubFundamentalsProvider:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass(frozen=True)
class EarningsEvent:
    """Un evento de earnings (fecha reportada o estimada) de Finnhub."""

    ticker: str
    earnings_date: date
    hour: str                      # 'bmo' | 'amc' | 'dmh' | ''
    eps_estimate: float | None
    eps_actual: float | None
    revenue_estimate: float | None
    revenue_actual: float | None


class FinnhubEarningsProvider:
    """Calendario de earnings sobre Finnhub `/calendar/earnings` (free tier)."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.finnhub_api_key:
            raise FinnhubError("FINNHUB_API_KEY no configurada.")
        self._api_key = settings.finnhub_api_key
        self._client = client or httpx.Client(
            base_url=settings.finnhub_base_url,
            timeout=httpx.Timeout(15.0),
        )

    def fetch_upcoming(self, ticker: str, horizon_days: int = 120) -> list[EarningsEvent]:
        """Earnings de `ticker` desde hoy hasta +horizon_days."""
        today = datetime.now(UTC).date()
        try:
            resp = self._client.get(
                "/calendar/earnings",
                params={
                    "from": today.isoformat(),
                    "to": (today + timedelta(days=horizon_days)).isoformat(),
                    "symbol": ticker,
                    "token": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise FinnhubError(
                f"Fallo consultando earnings de {ticker}: {exc}"
            ) from exc

        out: list[EarningsEvent] = []
        for e in (data or {}).get("earningsCalendar", []):
            raw_date = e.get("date")
            if not raw_date:
                continue
            # Guardamos bajo el ticker consultado: Finnhub a veces devuelve el
            # símbolo local (2330.TW por TSM, GOOGL por GOOG) pero es la misma
            # empresa, y así matchea con nuestros holdings.
            out.append(
                EarningsEvent(
                    ticker=ticker,
                    earnings_date=date.fromisoformat(raw_date),
                    hour=e.get("hour") or "",
                    eps_estimate=_num(e.get("epsEstimate")),
                    eps_actual=_num(e.get("epsActual")),
                    revenue_estimate=_num(e.get("revenueEstimate")),
                    revenue_actual=_num(e.get("revenueActual")),
                )
            )
        return out

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FinnhubEarningsProvider:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
