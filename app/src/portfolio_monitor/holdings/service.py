"""HoldingsSyncService: posiciones de IBKR → tabla holdings (§11.3).

Read-only. El gateway es async (`ib_async`), así que el sync es async; el
scheduler (§2) lo orquestará. `from_engine()` cablea el repo concreto.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine

from ..data.ibkr import Position
from ..db.repositories import HoldingsRepository
from ..logging import get_logger

logger = get_logger(__name__)


class PositionSource(Protocol):
    async def fetch_positions(self) -> list[Position]: ...


class HoldingsSink(Protocol):
    def upsert_positions(self, positions: list[Position]) -> int: ...


class HoldingsSyncService:
    """Trae posiciones de una fuente read-only y las upserta en holdings."""

    def __init__(self, source: PositionSource, holdings: HoldingsSink) -> None:
        self._source = source
        self._holdings = holdings

    @classmethod
    def from_engine(cls, source: PositionSource, engine: Engine) -> HoldingsSyncService:
        """Construye el service cableando el repo concreto sobre `engine`."""
        return cls(source=source, holdings=HoldingsRepository(engine))

    async def sync_once(self) -> int:
        """Un sync completo. Devuelve cuántas posiciones se aplicaron."""
        positions = await self._source.fetch_positions()
        if not positions:
            logger.info("Sync de holdings: IBKR no devolvió posiciones.")
            return 0
        applied = self._holdings.upsert_positions(positions)
        logger.info(
            "Sync de holdings: %d/%d posiciones aplicadas.", applied, len(positions)
        )
        return applied
