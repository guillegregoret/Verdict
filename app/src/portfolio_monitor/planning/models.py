"""Modelos del planificador de despliegue de cash (/plan).

El código arma este scaffold cuantitativo (cash por cuenta, gap-a-target en $,
tramos, gatillos); el reasoner prioriza y narra encima. Todo read-only: prepara
la decisión, no la ejecuta.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeploymentCandidate:
    """Una posición candidata a recibir cash (veredicto de compra)."""

    ticker: str
    verdict: str
    weight: float                 # % del portfolio hoy
    target_pct: float | None      # objetivo del usuario (si lo cargó)
    gap_usd: float                # $ para llegar al target (0 si está en/encima)
    price: float
    market_value: float
    unrealized_pct: float | None
    cluster: str | None
    cluster_weight: float         # % del portfolio del cluster (concentración)
    pe: float | None
    dip_threshold_pct: float      # umbral de caída que gatilla el próximo tramo
    tranche_usd: float            # tamaño de cada tramo (DCA)
    suggested_usd: float          # $ total sugerido para este nombre (0 si sin señal)

    @property
    def tranches(self) -> int:
        if self.tranche_usd <= 0 or self.suggested_usd <= 0:
            return 0
        return max(1, round(self.suggested_usd / self.tranche_usd))


@dataclass(frozen=True)
class AccountPlan:
    """Plan de despliegue para una cuenta IBKR (el cash no cruza cuentas)."""

    account_name: str
    ibkr_id: str
    available_cash: float
    reserve_usd: float            # colchón que NO se despliega
    deployable_usd: float         # available - reserve
    allocated_usd: float          # suma de los suggested_usd de los candidatos
    candidates: tuple[DeploymentCandidate, ...] = ()
    note: str | None = None       # ej: "targets = peso actual → sin señal de rebalanceo"


@dataclass(frozen=True)
class DeploymentPlan:
    """Plan integral de despliegue de cash across cuentas."""

    total_cash: float
    total_deployable: float
    total_allocated: float
    accounts: tuple[AccountPlan, ...] = ()
    targets_set: bool = False     # ¿el usuario cargó targets distintos del peso actual?
    note: str | None = None

    @property
    def has_candidates(self) -> bool:
        return any(a.candidates for a in self.accounts)
