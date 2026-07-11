"""Fundamentals autoritativos (CLAUDE.md §3, §5.3).

Abstracción `FundamentalsProvider` + implementación concreta de **FMP**. El
provider definitivo está abierto (§13: EDGAR vs FMP vs Finnhub); FMP es el primer
corte porque su REST devuelve ratios ya calculados. EDGAR (XBRL companyfacts)
puede sumarse luego detrás del mismo protocolo.

Guarda el payload crudo en `raw` por si el mapeo de campos cambia entre planes/
versiones de la API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Fundamentals:
    """Snapshot de fundamentals normalizado para el chequeo de tesis (§5.3)."""

    ticker: str
    pe: float | None
    revenue_growth: float | None
    gross_margin: float | None
    debt_to_equity: float | None
    raw: dict[str, Any]
    source: str


class FundamentalsError(RuntimeError):
    """Error al obtener fundamentals de un provider."""


class FundamentalsProvider(Protocol):
    def fetch(self, ticker: str) -> Fundamentals | None: ...


def _num(value: Any) -> float | None:
    """Coerciona a float; devuelve None si es None o no numérico."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FMPFundamentalsProvider:
    """Provider de fundamentals sobre Financial Modeling Prep.

    Usa `ratios-ttm` (pe, gross margin, debt/equity) + `financial-growth`
    (revenue growth). Los nombres de campo pueden variar según el plan de FMP;
    por eso se persiste el `raw`.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.fmp_api_key:
            raise FundamentalsError("FMP_API_KEY no configurada.")
        self._api_key = settings.fmp_api_key
        self._client = client or httpx.Client(
            base_url=settings.fmp_base_url,
            timeout=httpx.Timeout(15.0),
        )

    def fetch(self, ticker: str) -> Fundamentals | None:
        """Trae y normaliza los fundamentals de un ticker (None si no hay datos)."""
        ratios = self._get(f"/ratios-ttm/{ticker}")
        if not ratios:
            logger.info("FMP sin ratios para %s.", ticker)
            return None
        growth = self._get(f"/financial-growth/{ticker}", params={"limit": 1})

        r: dict[str, Any] = ratios[0]
        g: dict[str, Any] = growth[0] if growth else {}
        return Fundamentals(
            ticker=ticker,
            pe=_num(r.get("peRatioTTM")),
            revenue_growth=_num(g.get("revenueGrowth")),
            gross_margin=_num(r.get("grossProfitMarginTTM")),
            debt_to_equity=_num(r.get("debtEquityRatioTTM")),
            raw={"ratios_ttm": r, "financial_growth": g},
            source="fmp",
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """GET a FMP; devuelve la lista JSON. Lanza FundamentalsError en fallo."""
        query = {"apikey": self._api_key, **(params or {})}
        try:
            resp = self._client.get(path, params=query)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise FundamentalsError(f"Fallo consultando FMP {path}: {exc}") from exc
        return data if isinstance(data, list) else []

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FMPFundamentalsProvider:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
