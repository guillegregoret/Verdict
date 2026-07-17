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
from ..data.ibkr import AccountCash, IBKRClient, IBKRError, Position
from ..db.repositories import (
    AccountCashLike,
    CashRepository,
    HoldingsRepository,
    PositionLike,
)
from ..logging import get_logger

logger = get_logger(__name__)


# ── Protocolos: desacoplan el sync del cliente/repo concretos (testabilidad) ──
class PositionSource(Protocol):
    async def connect(self) -> None: ...
    async def fetch_positions(self) -> Sequence[Position]: ...
    async def fetch_cash(self) -> Sequence[AccountCash]: ...
    def disconnect(self) -> None: ...


class HoldingsSink(Protocol):
    def upsert_positions(self, positions: Sequence[PositionLike]) -> int: ...


class CashSink(Protocol):
    def upsert_snapshots(self, snapshots: Sequence[AccountCashLike]) -> int: ...


class HoldingsSyncService:
    """Trae posiciones + cash del gateway y los sincroniza a Postgres.

    Best-effort: si el gateway no está disponible (login/2FA pendiente, caído),
    loguea y devuelve 0 sin propagar — el sync nunca debe tumbar el loop. El cash
    (§5.4) se trae en la misma sesión para no abrir una segunda conexión.
    """

    def __init__(
        self,
        settings: Settings,
        source_factory: Callable[[], PositionSource],
        holdings: HoldingsSink,
        cash: CashSink | None = None,
    ) -> None:
        self._settings = settings
        self._source_factory = source_factory
        self._holdings = holdings
        self._cash = cash

    @classmethod
    def from_engine(cls, settings: Settings, engine: Engine) -> HoldingsSyncService:
        """Cablea un `IBKRClient` nuevo por sync + los repos de holdings y cash."""
        return cls(
            settings=settings,
            source_factory=lambda: IBKRClient(settings),
            holdings=HoldingsRepository(engine),
            cash=CashRepository(engine),
        )

    def run_once(self) -> int:
        """Un ciclo de sync. Devuelve cuántas posiciones se aplicaron."""
        try:
            positions, cash = asyncio.run(self._collect())
        except IBKRError as exc:
            logger.warning("Holdings sync: gateway no disponible: %s", exc)
            return 0
        applied = self._holdings.upsert_positions(positions)
        if self._cash is not None and cash:
            saved = self._cash.upsert_snapshots(cash)
            logger.info("Cash sync: %d cuentas actualizadas.", saved)
        logger.info(
            "Holdings sync: %d posiciones traídas, %d aplicadas.",
            len(positions),
            applied,
        )
        return applied

    async def _collect(self) -> tuple[Sequence[Position], Sequence[AccountCash]]:
        """Conecta (read-only), trae posiciones + cash y desconecta siempre."""
        source = self._source_factory()
        try:
            await source.connect()
            positions = await source.fetch_positions()
            cash = await source.fetch_cash() if self._cash is not None else []
            return positions, cash
        finally:
            source.disconnect()
