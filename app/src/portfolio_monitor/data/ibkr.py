"""Cliente IBKR vía ib_async + IB Gateway (CLAUDE.md §3).

🔴 READ-ONLY: solo sincroniza posiciones. Se conecta con readonly=True y jamás
llama a métodos de órdenes. El gateway además corre con READ_ONLY_API=yes.

`ib_async` se importa de forma perezosa (dentro de `__init__`) para que el resto
del código y los tests no requieran la dependencia si inyectan un IB fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Position:
    """Posición normalizada desde IBKR."""

    account: str          # id de cuenta IBKR (ej: U22106929)
    ticker: str           # símbolo del contrato
    shares: float
    avg_cost: float
    company: str | None = None


class IBKRError(RuntimeError):
    """Error al hablar con el IB Gateway."""


class IBKRClient:
    """Wrapper read-only sobre `ib_async.IB` para sincronizar posiciones."""

    def __init__(self, settings: Settings, ib: Any | None = None) -> None:
        self._settings = settings
        if ib is not None:
            self._ib = ib
        else:  # import perezoso: solo en uso real, no en tests con fake
            from ib_async import IB  # noqa: PLC0415

            self._ib = IB()

    async def connect(self) -> None:
        """Conecta al gateway en modo READ-ONLY."""
        try:
            await self._ib.connectAsync(
                host=self._settings.ib_gateway_host,
                port=self._settings.ib_gateway_port,
                clientId=self._settings.ib_gateway_client_id,
                readonly=True,  # 🔴 el cliente no puede enviar órdenes
            )
        except Exception as exc:  # noqa: BLE001 - normalizamos cualquier fallo
            raise IBKRError(f"No se pudo conectar al IB Gateway: {exc}") from exc

    async def fetch_positions(self) -> list[Position]:
        """Trae las posiciones de todas las cuentas (read-only)."""
        try:
            raw = await self._ib.reqPositionsAsync()
        except Exception as exc:  # noqa: BLE001
            raise IBKRError(f"Fallo pidiendo posiciones: {exc}") from exc
        return [self._to_position(p) for p in raw]

    def disconnect(self) -> None:
        """Cierra la conexión con el gateway."""
        self._ib.disconnect()

    @staticmethod
    def _to_position(raw: Any) -> Position:
        """Mapea un `ib_async` Position a nuestro dataclass."""
        contract = raw.contract
        company = getattr(contract, "description", None) or getattr(
            contract, "longName", None
        )
        return Position(
            account=raw.account,
            ticker=contract.symbol,
            shares=float(raw.position),
            avg_cost=float(raw.avgCost),
            company=company,
        )

    async def __aenter__(self) -> IBKRClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.disconnect()
