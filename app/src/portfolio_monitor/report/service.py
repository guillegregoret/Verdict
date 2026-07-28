"""ReportService: entrega la reevaluación por email, con fallback a Telegram (§5).

Compone el review (contexto + análisis de Claude), lo renderiza a HTML branded y
lo manda por email (Resend). Devuelve un texto corto para Telegram ("📧 enviado").
Si el email no está configurado o falla, cae al texto completo del análisis para
que /reevaluar nunca quede sin respuesta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..logging import get_logger
from ..mailer import MailerError, ResendMailer
from ..reasoning import PortfolioReviewContext, ReasoningService
from ..review import PortfolioReviewService
from .renderer import PortfolioReportRenderer

logger = get_logger(__name__)


class ReviewLike(Protocol):
    def build_context(
        self, now: datetime | None = ...
    ) -> PortfolioReviewContext | None: ...
    def render_text(self, context: PortfolioReviewContext) -> str: ...


class MailerLike(Protocol):
    def send(self, subject: str, html: str, text: str | None = ...) -> None: ...
    @property
    def recipient(self) -> str: ...


class ReportService:
    """Orquesta la reevaluación y su entrega (email preferente, Telegram fallback)."""

    def __init__(
        self,
        review: ReviewLike,
        renderer: PortfolioReportRenderer,
        mailer: MailerLike | None = None,
    ) -> None:
        self._review = review
        self._renderer = renderer
        self._mailer = mailer

    @classmethod
    def from_engine(
        cls, engine: Engine, reasoning: ReasoningService, settings: Settings
    ) -> ReportService:
        mailer = ResendMailer(settings) if settings.email_configured else None
        return cls(
            review=PortfolioReviewService.from_engine(engine, reasoning),
            renderer=PortfolioReportRenderer(),
            mailer=mailer,
        )

    def deliver(self, now: datetime | None = None) -> str:
        """Genera y entrega el reporte. Devuelve el texto a mostrar en Telegram."""
        now = now or datetime.now(UTC)
        context = self._review.build_context(now)
        if context is None:
            return "No holdings loaded yet (waiting for the next positions sync)."

        analysis = self._review.render_text(context)
        if self._mailer is None:
            return analysis  # email no configurado → texto completo a Telegram

        try:
            self._mailer.send(
                subject=self._renderer.subject(context, now),
                html=self._renderer.render_html(context, analysis, now),
                text=self._renderer.render_text(context, analysis, now),
            )
        except MailerError as exc:
            logger.warning("Email falló (%s); devuelvo el texto a Telegram.", exc)
            return "⚠️ Email delivery failed; here's the report:\n\n" + analysis
        return f"📧 Full portfolio report sent to {self._mailer.recipient}."
