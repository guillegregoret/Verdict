"""Entrypoint del monolito.

Estado actual (§11.2): corre el data layer — price poller Finnhub → Postgres.
A medida que sumen módulos, el scheduler (§2) tomará la orquestación y este
entrypoint pasará a arrancarlo a él.
"""

from __future__ import annotations

from .config import get_settings
from .data.finnhub import FinnhubClient
from .db.engine import get_engine
from .logging import get_logger, setup_logging
from .poller import PricePoller


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("Portfolio Monitor arrancando (env=%s).", settings.env)

    engine = get_engine()
    with FinnhubClient(settings) as finnhub:
        poller = PricePoller.from_engine(settings=settings, engine=engine, quotes=finnhub)
        poller.run_forever()


if __name__ == "__main__":
    main()
