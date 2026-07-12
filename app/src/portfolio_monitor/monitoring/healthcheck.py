"""Dead-man's switch vía healthchecks.io o push de Uptime Kuma (§9).

La app pinga una URL cada tick del loop; si deja de pingar, el monitor avisa
(detecta el silencio, no un error explícito). Nunca lanza: el monitoreo no debe
tumbar el loop. Si la URL no está configurada, es un no-op.

Soporta ambos dialectos de "fallo":
- healthchecks.io: GET a `<url>/fail`.
- Uptime Kuma (URL contiene `/api/push/`): GET a `<url>?status=down`.
"""

from __future__ import annotations

import httpx

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)


class HealthcheckPinger:
    """Pinga healthchecks.io con éxito/fallo por tick."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._url = settings.healthchecks_ping_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0))

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def ping(self, success: bool = True) -> None:
        """Pinga OK o fallo según el dialecto de la URL. Silencioso ante errores."""
        if not self._url:
            return
        if success:
            target = self._url
        elif "/api/push/" in self._url:  # push monitor de Uptime Kuma
            target = f"{self._url}?status=down"
        else:  # healthchecks.io
            target = f"{self._url}/fail"
        try:
            self._client.get(target)
        except httpx.HTTPError as exc:  # nunca propagar: es monitoreo
            logger.warning("Ping del dead-man's switch falló: %s", exc)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HealthcheckPinger:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
