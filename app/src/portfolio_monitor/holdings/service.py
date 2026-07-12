"""Holdings Sync: posiciones IBKR (read-only) → Postgres (§11.3).

🔴 READ-ONLY: se conecta al gateway con `readonly=True`, trae posiciones y las
persiste. Nunca envía órdenes. El `upsert` preserva los campos de config del
usuario (verdict/thesis/cluster/target_pct): solo actualiza shares/avg_cost.

`ib_async` es async; acá aislamos ese detalle con `asyncio.run()` por sync, de
modo que el scheduler (síncrono) lo llame como cualquier otro `run_once()`. La
fuente de posiciones se inyecta vía factory para testear sin gateway ni loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..data.ibkr import IBKRClient, IBKRError, Position
from ..db.repositories import HoldingsRepository, PositionLike
from ..logging import get_logger

logger = get_logger(__name__)


# ── Protocolos: desacoplan el sync del cliente/repo concretos (testabilidad) ──
class PositionSource(Protocol):
    async def connect(self) -> None: ...
    async def fetch_positions(self) -> Sequence[Position]: ...
    def disconnect(self) -> None: ...


class HoldingsSink(Protocol):
    def upsert_positions(self, positions: Sequence[PositionLike]) -> int: ...


class HoldingsSyncService:
    """Trae posiciones del gateway y las sincroniza a Postgres.

    Best-effort: si el gateway no está disponible (login/2FA pendiente, caído),
    loguea y devuelve 0 sin propagar — el sync nunca debe tumbar el loop.
    """

    def __init__(
        self,
        settings: Settings,
        source_factory: Callable[[], PositionSource],
        holdings: HoldingsSink,
    ) -> None:
        self._settings = settings
        self._source_factory = source_factory
        self._holdings = holdings

    @classmethod
    def from_engine(cls, settings: Settings, engine: Engine) -> HoldingsSyncService:
        """Cablea un `IBKRClient` nuevo por sync + el `HoldingsRepository`."""
        return cls(
            settings=settings,
            source_factory=lambda: IBKRClient(settings),
            holdings=HoldingsRepository(engine),
        )

    def run_once(self) -> int:
        """Un ciclo de sync. Devuelve cuántas posiciones se aplicaron."""
        try:
            positions = asyncio.run(self._collect())
        except IBKRError as exc:
            logger.warning("Holdings sync: gateway no disponible: %s", exc)
            return 0
        applied = self._holdings.upsert_positions(positions)
        logger.info(
            "Holdings sync: %d posiciones traídas, %d aplicadas.",
            len(positions),
            applied,
        )
        return applied

    async def _collect(self) -> Sequence[Position]:
        """Conecta (read-only), trae posiciones y desconecta siempre."""
        source = self._source_factory()
        try:
            await source.connect()
            return await source.fetch_positions()
        finally:
            source.disconnect()
