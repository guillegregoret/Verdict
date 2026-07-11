"""Dead-man's switch vía healthchecks.io (§9).

La app pinga una URL cada tick del loop; si deja de pingar, healthchecks.io avisa
(detecta el silencio, no un error explícito). Nunca lanza: el monitoreo no debe
tumbar el loop. Si la URL no está configurada, es un no-op.
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
        """Pinga OK (URL base) o fallo (URL + /fail). Silencioso ante errores."""
        if not self._url:
            return
        target = self._url if success else f"{self._url}/fail"
        try:
            self._client.get(target)
        except httpx.HTTPError as exc:  # nunca propagar: es monitoreo
            logger.warning("Ping a healthchecks.io falló: %s", exc)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HealthcheckPinger:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
