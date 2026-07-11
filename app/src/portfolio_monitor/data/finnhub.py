"""Cliente Finnhub: quotes (backbone de precios, CLAUDE.md §3).

Solo lo necesario para el price poller. Fundamentals y news se sumarán en sus
módulos (§11.5). Free tier ~60 req/min: el spacing lo maneja el poller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..config import Settings


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
