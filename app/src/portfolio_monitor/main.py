"""Entrypoint del monolito: arranca el scheduler que corre el loop completo (§2).

Cada tick: (sync de holdings IBKR + refresh de fundamentals, throttleados) →
price poller → señales (movimiento de precio + deterioro de fundamentals) →
reasoning → Telegram. Los jobs periódicos son best-effort: si fallan, loguean
y siguen sin tumbar el loop.
"""

from __future__ import annotations

from contextlib import ExitStack

from .config import Settings, get_settings
from .data.finnhub import (
    FinnhubClient,
    FinnhubEarningsProvider,
    FinnhubFundamentalsProvider,
)
from .db.engine import get_engine
from .db.repositories import TickerConfigRepository
from .earnings import EarningsService
from .fundamentals import (
    FundamentalsMonitor,
    FundamentalsRefreshService,
    FundamentalsService,
)
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
    with ExitStack() as stack:
        finnhub = stack.enter_context(FinnhubClient(settings))
        notifier = stack.enter_context(TelegramNotifier(settings))
        pinger = stack.enter_context(HealthcheckPinger(settings))

        poller = PricePoller.from_engine(
            settings=settings, engine=engine, quotes=finnhub
        )
        reasoning = _build_reasoning(settings)

        # Fundamentals (§5.3): mismo provider Finnhub para el fetch on-trigger,
        # el refresh periódico (historial) y el monitor de deterioro.
        provider = stack.enter_context(FinnhubFundamentalsProvider(settings))
        fundamentals = FundamentalsService.from_engine(
            provider, engine, max_age_hours=settings.fundamentals_max_age_hours
        )
        fundamentals_refresh = FundamentalsRefreshService(
            fundamentals, TickerConfigRepository(engine)
        )
        fundamentals_monitor = FundamentalsMonitor.from_engine(engine, settings)

        # Calendario de earnings (§5 informativo): mismo Finnhub, refresh diario.
        earnings_provider = stack.enter_context(FinnhubEarningsProvider(settings))
        earnings_refresh = EarningsService.from_engine(
            earnings_provider, engine, horizon_days=settings.earnings_horizon_days
        )

        pipeline = AlertPipeline.from_engine(
            engine,
            reasoning,
            notifier,
            fundamentals=fundamentals,
            fundamentals_monitor=fundamentals_monitor,
        )
        holdings_sync = HoldingsSyncService.from_engine(settings, engine)
        Scheduler(
            settings=settings,
            poller=poller,
            pipeline=pipeline,
            pinger=pinger,
            holdings_sync=holdings_sync,
            fundamentals_refresh=fundamentals_refresh,
            earnings_refresh=earnings_refresh,
        ).run_forever()


if __name__ == "__main__":
    main()
