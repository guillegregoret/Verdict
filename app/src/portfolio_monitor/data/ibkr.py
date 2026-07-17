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


@dataclass(frozen=True)
class AccountCash:
    """Cash disponible de una cuenta IBKR (para el DCA, §5.4)."""

    account: str          # id de cuenta IBKR
    total_cash: float     # TotalCashValue
    available_funds: float  # AvailableFunds (lo que se puede desplegar)
    currency: str = "USD"


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

    async def fetch_cash(self) -> list[AccountCash]:
        """Trae el cash disponible por cuenta (accountSummary, read-only)."""
        try:
            raw = await self._ib.accountSummaryAsync()
        except Exception as exc:  # noqa: BLE001
            raise IBKRError(f"Fallo pidiendo cash: {exc}") from exc
        return self._to_cash(raw)

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

    @staticmethod
    def _to_cash(raw: Any) -> list[AccountCash]:
        """Agrega los AccountValue de accountSummary a un cash por cuenta (USD)."""
        wanted = {"TotalCashValue", "AvailableFunds"}
        by_account: dict[str, dict[str, tuple[float, str]]] = {}
        for v in raw:
            if v.tag not in wanted:
                continue
            try:
                value = float(v.value)
            except (TypeError, ValueError):
                continue
            tags = by_account.setdefault(v.account, {})
            # Preferimos USD; si no, guardamos lo que haya como fallback.
            if v.tag not in tags or v.currency == "USD":
                tags[v.tag] = (value, v.currency or "USD")

        out: list[AccountCash] = []
        for account, tags in by_account.items():
            total = tags.get("TotalCashValue")
            avail = tags.get("AvailableFunds")
            if total is None and avail is None:
                continue
            out.append(
                AccountCash(
                    account=account,
                    total_cash=total[0] if total else 0.0,
                    available_funds=avail[0] if avail else 0.0,
                    currency=(avail or total)[1],
                )
            )
        return out

    async def __aenter__(self) -> IBKRClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.disconnect()
