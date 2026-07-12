"""Entrypoint del monolito: arranca el scheduler que corre el loop completo (§2).

Cada tick: (sync de holdings IBKR, throttleado) → price poller → trigger →
fundamentals → reasoning → Telegram. El sync de holdings (§11.3) es best-effort:
si el gateway no logueó (2FA pendiente), loguea y sigue sin tumbar el loop.
"""

from __future__ import annotations

from .config import Settings, get_settings
from .data.finnhub import FinnhubClient
from .db.engine import get_engine
from .holdings import HoldingsSyncService
from .logging import get_logger, setup_logging
from .monitoring import HealthcheckPinger
from .notifier import TelegramNotifier
from .poller import PricePoller
from .reasoning import AnthropicReasoner, ReasoningService, TemplateReasoner
from .scheduler import AlertPipeline, Scheduler

logger = get_logger(__name__)


def _build_reasoning(settings: Settings) -> ReasoningService:
    """Anthropic como primario con fallback a template; template solo si no hay key."""
    fallback = TemplateReasoner()
    if settings.anthropic_api_key:
        return ReasoningService(primary=AnthropicReasoner(settings), fallback=fallback)
    logger.warning("Sin ANTHROPIC_API_KEY: el reasoner usará solo el template.")
    return ReasoningService(primary=fallback)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Portfolio Monitor arrancando (env=%s).", settings.env)

    engine = get_engine()
    with (
        FinnhubClient(settings) as finnhub,
        TelegramNotifier(settings) as notifier,
        HealthcheckPinger(settings) as pinger,
    ):
        poller = PricePoller.from_engine(
            settings=settings, engine=engine, quotes=finnhub
        )
        reasoning = _build_reasoning(settings)
        pipeline = AlertPipeline.from_engine(engine, reasoning, notifier)
        holdings_sync = HoldingsSyncService.from_engine(settings, engine)
        Scheduler(
            settings=settings,
            poller=poller,
            pipeline=pipeline,
            pinger=pinger,
            holdings_sync=holdings_sync,
        ).run_forever()


if __name__ == "__main__":
    main()
