"""ResendMailer: cliente mínimo de la API de Resend para mandar HTML.

Envía un email (`POST /emails`) con `from`, `to`, `subject`, `html`. El dominio
se autentica vía DNS en Cloudflare (SPF/DKIM/DMARC). Errores se normalizan a
`MailerError` para que el orquestador pueda caer al texto en Telegram.
"""

from __future__ import annotations

import httpx

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)


class MailerError(RuntimeError):
    """Error al enviar un email."""


class ResendMailer:
    """Cliente de Resend para enviar reportes HTML por email."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.email_configured:
            raise MailerError(
                "Email no configurado (RESEND_API_KEY / EMAIL_FROM / EMAIL_TO)."
            )
        self._from = settings.email_from
        self._to = settings.email_to
        self._client = client or httpx.Client(
            base_url=settings.resend_base_url,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            timeout=httpx.Timeout(15.0),
        )

    @property
    def recipient(self) -> str:
        return self._to

    def send(self, subject: str, html: str, text: str | None = None) -> None:
        """Envía un email HTML (con fallback de texto plano opcional)."""
        payload: dict[str, object] = {
            "from": self._from,
            "to": [self._to],
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text
        try:
            resp = self._client.post("/emails", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise MailerError(f"Fallo enviando email vía Resend: {exc}") from exc
        logger.info("Reporte enviado por email a %s.", self._to)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ResendMailer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
