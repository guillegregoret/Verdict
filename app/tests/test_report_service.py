"""Tests del ReportService: email preferente con fallback a Telegram."""

from __future__ import annotations

from datetime import datetime

from portfolio_monitor.mailer import MailerError
from portfolio_monitor.reasoning import PortfolioReviewContext
from portfolio_monitor.report import PortfolioReportRenderer, ReportService


def _context() -> PortfolioReviewContext:
    return PortfolioReviewContext(
        positions_block="• NVDA 18.0% [Mantener] — P/E 31",
        cash_block="• IA: $69 USD",
        total_value=23000.0,
        total_cash=564.0,
        position_count=1,
    )


class FakeReview:
    def __init__(self, context: PortfolioReviewContext | None) -> None:
        self._context = context

    def build_context(self, now: datetime | None = None) -> PortfolioReviewContext | None:
        return self._context

    def render_text(self, context: PortfolioReviewContext) -> str:
        return "ANALYSIS BODY"


class FakeMailer:
    def __init__(self, error: bool = False) -> None:
        self._error = error
        self.sent: list[tuple[str, str]] = []

    @property
    def recipient(self) -> str:
        return "me@example.com"

    def send(self, subject: str, html: str, text: str | None = None) -> None:
        if self._error:
            raise MailerError("boom")
        self.sent.append((subject, html))


_MISSING = object()


def _service(context=_MISSING, mailer: FakeMailer | None = None) -> ReportService:
    return ReportService(
        review=FakeReview(_context() if context is _MISSING else context),
        renderer=PortfolioReportRenderer(),
        mailer=mailer,
    )


def test_no_holdings_message() -> None:
    out = _service(context=None).deliver()
    assert "No holdings" in out


def test_without_mailer_returns_full_text() -> None:
    out = _service(mailer=None).deliver()
    assert out == "ANALYSIS BODY"


def test_with_mailer_sends_and_returns_notice() -> None:
    mailer = FakeMailer()
    out = _service(mailer=mailer).deliver()
    assert "sent to me@example.com" in out
    assert len(mailer.sent) == 1
    subject, html = mailer.sent[0]
    assert "Verdict" in subject
    assert "GREGORET INDUSTRIES" in html  # se renderizó el HTML branded


def test_mailer_error_falls_back_to_text() -> None:
    out = _service(mailer=FakeMailer(error=True)).deliver()
    assert "failed" in out.lower()
    assert "ANALYSIS BODY" in out
