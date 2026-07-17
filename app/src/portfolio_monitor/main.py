"""Entrypoint del monolito: arranca el scheduler que corre el loop completo (§2).

Cada tick: (sync de holdings IBKR + refresh de fundamentals, throttleados) →
price poller → señales (movimiento de precio + deterioro de fundamentals) →
reasoning → Telegram. Los jobs periódicos son best-effort: si fallan, loguean
y siguen sin tumbar el loop.
"""

from __future__ import annotations

import threading
from contextlib import ExitStack

from .config import Settings, get_settings
from .data.finnhub import (
    FinnhubClient,
    FinnhubEarningsProvider,
    FinnhubFundamentalsProvider,
    FinnhubRatingsProvider,
)
from .db.engine import get_engine
from .db.repositories import TickerConfigRepository
from .dca import DcaSizer
from .digests import WeeklyDigestRunner
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
from .ratings import RatingsMonitor, RatingsService
from .reasoning import AnthropicReasoner, ReasoningService, TemplateReasoner
from .scheduler import AlertPipeline, Scheduler
from .telegram_bot import CommandRouter, TelegramBot

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
        # Monitores (§5): cada uno emite señales con su propio cooldown.
        monitors = [
            FundamentalsMonitor.from_engine(engine, settings),
            RatingsMonitor.from_engine(engine, settings),
        ]

        # Calendario de earnings (§5 informativo): mismo Finnhub, refresh diario.
        earnings_provider = stack.enter_context(FinnhubEarningsProvider(settings))
        earnings_refresh = EarningsService.from_engine(
            earnings_provider, engine, horizon_days=settings.earnings_horizon_days
        )
        # Ratings de analistas (§5): refresh diario del consenso.
        ratings_provider = stack.enter_context(FinnhubRatingsProvider(settings))
        ratings_refresh = RatingsService.from_engine(ratings_provider, engine)

        # Avisos semanales (§5): lunes earnings + viernes resumen del portfolio.
        weekly_digest = (
            WeeklyDigestRunner.from_engine(engine, notifier, settings)
            if settings.weekly_digests_enabled
            else None
        )

        # DCA (§5.4): sizing sugerido en dips, capado al cash de la cuenta.
        dca = DcaSizer.from_engine(engine, settings) if settings.dca_enabled else None

        # Bot interactivo /status (§5): thread daemon, long-polling, read-only.
        if settings.telegram_bot_enabled and settings.telegram_bot_token:
            bot = TelegramBot(settings, CommandRouter.from_engine(engine))
            threading.Thread(
                target=bot.run_forever, name="telegram-bot", daemon=True
            ).start()

        pipeline = AlertPipeline.from_engine(
            engine,
            reasoning,
            notifier,
            fundamentals=fundamentals,
            monitors=monitors,
            dca=dca,
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
            ratings_refresh=ratings_refresh,
            weekly_digest=weekly_digest,
        ).run_forever()


if __name__ == "__main__":
    main()
