"""Poller de status.json de proveedores (Atlassian Statuspage, §9).

Consulta el `/api/v2/status.json` de un proveedor (ej: Finnhub) para auto-clasificar
"caída de ellos" vs "bug nuestro". Mapea el indicator de Statuspage a nuestro
vocabulario de salud ('up' | 'degraded' | 'down').
"""

from __future__ import annotations

import httpx

from ..logging import get_logger

logger = get_logger(__name__)

# indicator de Statuspage → estado interno
_INDICATOR_MAP = {
    "none": "up",
    "minor": "degraded",
    "major": "degraded",
    "critical": "down",
}


class MonitoringError(RuntimeError):
    """Error consultando un status page."""


class StatusPagePoller:
    """Lee el indicator de un status.json estilo Atlassian Statuspage."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0))

    def poll(self, status_url: str) -> str:
        """Devuelve 'up' | 'degraded' | 'down' según el status page."""
        try:
            resp = self._client.get(status_url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise MonitoringError(f"Fallo consultando {status_url}: {exc}") from exc
        indicator = (data.get("status") or {}).get("indicator", "none")
        return _INDICATOR_MAP.get(indicator, "degraded")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> StatusPagePoller:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
