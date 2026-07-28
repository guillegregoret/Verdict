"""Mailer: envío de reportes por email vía Resend (§5).

Cloudflare gestiona el DNS (SPF/DKIM/DMARC) para autenticar el dominio; Resend
hace el envío. Si el email no está configurado, el reporte cae al texto en
Telegram — el mailer nunca es obligatorio.
"""

from .resend import MailerError, ResendMailer

__all__ = ["MailerError", "ResendMailer"]
