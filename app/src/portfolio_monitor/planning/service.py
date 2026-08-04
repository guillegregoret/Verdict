"""CashPlanService: arma el plan de despliegue de cash (parte determinística).

Calcula, por cuenta IBKR (el cash no cruza cuentas), cuánto desplegar y en qué
candidatos con veredicto de compra, cerrando el gap contra el target del usuario,
troceado en tramos con su gatillo de caída. Read-only: sugiere, no ejecuta.

Cuando los targets = peso actual (seed sin editar) no hay señal de rebalanceo: el
plan NO inventa una asignación; lista los candidatos con sus métricas para que el
reasoner priorice y le avisa al usuario que cargue targets reales.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine

from ..config import Settings
from ..db.repositories import (
    AccountCashRow,
    CashRepository,
    DcaPlan,
    DcaPlanRepository,
    FundamentalsRepository,
    FundamentalsRow,
    HoldingPosition,
    HoldingsRepository,
    PriceRepository,
    TickerConfig,
    TickerConfigRepository,
)
from ..logging import get_logger
from ..trigger.verdict_gate import allows_buy
from .models import AccountPlan, DeploymentCandidate, DeploymentPlan

logger = get_logger(__name__)

# Drift (pp) mínimo para considerar que el usuario cargó targets "de verdad"
# (distintos del peso actual seedeado). Por debajo → sin señal de rebalanceo.
_TARGET_SIGNAL_PP = 0.5


class PositionSource(Protocol):
    def positions(self) -> list[HoldingPosition]: ...


class PriceReader(Protocol):
    def latest_price(self, ticker: str) -> float | None: ...


class CashReader(Protocol):
    def latest(self) -> list[AccountCashRow]: ...


class FundReader(Protocol):
    def latest(self, ticker: str) -> FundamentalsRow | None: ...


class DcaReader(Protocol):
    def plans_by_ticker(self) -> dict[str, DcaPlan]: ...


class ConfigReader(Protocol):
    def enabled_configs(self) -> list[TickerConfig]: ...


class PlanReasoner(Protocol):
    def plan(self, plan: DeploymentPlan) -> object: ...  # Suggestion (con .text)


class CashPlanService:
    """Ensambla el DeploymentPlan determinístico a partir de la DB."""

    def __init__(
        self,
        holdings: PositionSource,
        prices: PriceReader,
        cash: CashReader,
        fundamentals: FundReader,
        dca: DcaReader,
        configs: ConfigReader,
        settings: Settings,
    ) -> None:
        self._holdings = holdings
        self._prices = prices
        self._cash = cash
        self._fundamentals = fundamentals
        self._dca = dca
        self._configs = configs
        self._settings = settings

    @classmethod
    def from_engine(cls, engine: Engine, settings: Settings) -> CashPlanService:
        return cls(
            holdings=HoldingsRepository(engine),
            prices=PriceRepository(engine),
            cash=CashRepository(engine),
            fundamentals=FundamentalsRepository(engine),
            dca=DcaPlanRepository(engine),
            configs=TickerConfigRepository(engine),
            settings=settings,
        )

    def build_plan(self, now: datetime | None = None) -> DeploymentPlan:
        s = self._settings
        positions = self._holdings.positions()
        prices = {p.ticker: self._prices.latest_price(p.ticker) for p in positions}

        # Valor de mercado por posición y total del portfolio.
        mv: dict[str, float] = {}
        total = 0.0
        for p in positions:
            price = prices.get(p.ticker)
            if price is None or p.shares is None:
                continue
            v = p.shares * price
            mv[p.ticker] = v
            total += v

        cluster_w = self._cluster_weights(positions, mv, total)
        targets_set = self._targets_set(positions, mv, total)
        dca_plans = self._dca.plans_by_ticker()
        thresholds = {c.ticker: c.threshold_pct for c in self._configs.enabled_configs()}
        cash_by_account = {r.ibkr_id: r for r in self._cash.latest()}

        by_account: dict[str, list[HoldingPosition]] = {}
        for p in positions:
            by_account.setdefault(p.ibkr_id, []).append(p)

        account_plans: list[AccountPlan] = []
        grand_deployable = grand_allocated = grand_cash = 0.0
        for ibkr_id, cash_row in cash_by_account.items():
            available = cash_row.available_funds
            grand_cash += available
            reserve = min(available, max(available * s.cash_reserve_pct, s.cash_reserve_floor_usd))
            deployable = max(0.0, available - reserve)

            cands = self._candidates(
                by_account.get(ibkr_id, []), prices, mv, total, cluster_w,
                targets_set, dca_plans, thresholds,
            )
            cands = self._allocate(cands, deployable, targets_set)
            allocated = round(sum(c.suggested_usd for c in cands), 2)
            grand_deployable += deployable
            grand_allocated += allocated

            note = None
            if cands and not targets_set:
                note = ("targets = peso actual (sin editar) → sin señal de rebalanceo; "
                        "ordeno por valuación y te dejo priorizar. Cargá targets reales "
                        "para un plan por gap.")
            account_plans.append(
                AccountPlan(
                    account_name=cash_row.name,
                    ibkr_id=ibkr_id,
                    available_cash=round(available, 2),
                    reserve_usd=round(reserve, 2),
                    deployable_usd=round(deployable, 2),
                    allocated_usd=allocated,
                    candidates=tuple(cands),
                    note=note,
                )
            )

        account_plans.sort(key=lambda a: a.deployable_usd, reverse=True)
        return DeploymentPlan(
            total_cash=round(grand_cash, 2),
            total_deployable=round(grand_deployable, 2),
            total_allocated=round(grand_allocated, 2),
            accounts=tuple(account_plans),
            targets_set=targets_set,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _cluster_weights(
        self, positions: list[HoldingPosition], mv: dict[str, float], total: float
    ) -> dict[str, float]:
        acc: dict[str, float] = {}
        for p in positions:
            if p.ticker in mv:
                acc[p.cluster or "Sin clasificar"] = acc.get(
                    p.cluster or "Sin clasificar", 0.0
                ) + mv[p.ticker]
        return {c: (v / total * 100 if total else 0.0) for c, v in acc.items()}

    def _targets_set(
        self, positions: list[HoldingPosition], mv: dict[str, float], total: float
    ) -> bool:
        """True si algún target difiere del peso actual > _TARGET_SIGNAL_PP (editado)."""
        for p in positions:
            if p.target_pct is None or p.ticker not in mv or total == 0:
                continue
            weight = mv[p.ticker] / total * 100
            if abs(weight - p.target_pct) >= _TARGET_SIGNAL_PP:
                return True
        return False

    def _candidates(
        self,
        positions: list[HoldingPosition],
        prices: dict[str, float | None],
        mv: dict[str, float],
        total: float,
        cluster_w: dict[str, float],
        targets_set: bool,
        dca_plans: dict[str, DcaPlan],
        thresholds: dict[str, float],
    ) -> list[DeploymentCandidate]:
        s = self._settings
        out: list[DeploymentCandidate] = []
        for p in positions:
            price = prices.get(p.ticker)
            if not allows_buy(p.verdict) or price is None or p.shares is None:
                continue
            value = mv.get(p.ticker, 0.0)
            weight = value / total * 100 if total else 0.0
            gap = 0.0
            if targets_set and p.target_pct is not None and total:
                gap = max(0.0, p.target_pct / 100 * total - value)
            f = self._fundamentals.latest(p.ticker)
            pnl = None
            if p.avg_cost:
                pnl = (price - p.avg_cost) / p.avg_cost * 100
            tranche = dca_plans[p.ticker].tranche_usd if p.ticker in dca_plans \
                else s.dca_default_tranche_usd
            out.append(
                DeploymentCandidate(
                    ticker=p.ticker,
                    verdict=p.verdict,
                    weight=round(weight, 2),
                    target_pct=p.target_pct,
                    gap_usd=round(gap, 2),
                    price=round(price, 2),
                    market_value=round(value, 2),
                    unrealized_pct=round(pnl, 1) if pnl is not None else None,
                    cluster=p.cluster,
                    cluster_weight=round(cluster_w.get(p.cluster or "Sin clasificar", 0.0), 1),
                    pe=f.pe if f is not None else None,
                    dip_threshold_pct=thresholds.get(p.ticker, -2.7),
                    tranche_usd=round(tranche, 2),
                    suggested_usd=0.0,
                )
            )
        return out

    def _allocate(
        self, cands: list[DeploymentCandidate], deployable: float, targets_set: bool
    ) -> list[DeploymentCandidate]:
        """Asigna `deployable` proporcional al gap (cap al gap). Sin señal → 0."""
        total_gap = sum(c.gap_usd for c in cands)
        if targets_set and total_gap > 0 and deployable > 0:
            for i, c in enumerate(cands):
                if total_gap <= deployable:
                    alloc = c.gap_usd
                else:
                    alloc = deployable * c.gap_usd / total_gap
                cands[i] = replace(c, suggested_usd=round(alloc, 2))
            cands.sort(key=lambda c: c.suggested_usd, reverse=True)
        else:
            # Sin señal de target: no inventamos asignación; orden por valuación
            # (P/E asc, None al final) para que el reasoner/usuario priorice.
            cands.sort(key=lambda c: (c.pe is None, c.pe if c.pe is not None else 0.0))
        return cands


class PlanService:
    """Orquesta /plan: build determinístico → el reasoner prioriza/narra encima.

    Desacoplado de reasoning vía `PlanReasoner` (Protocol) para no crear ciclo de
    imports; main.py/commands le pasan el ReasoningService concreto.
    """

    def __init__(self, planner: CashPlanService, reasoner: PlanReasoner) -> None:
        self._planner = planner
        self._reasoner = reasoner

    @classmethod
    def from_engine(
        cls, engine: Engine, reasoner: PlanReasoner, settings: Settings
    ) -> PlanService:
        return cls(CashPlanService.from_engine(engine, settings), reasoner)

    def deliver(self, now: datetime | None = None) -> str:
        plan = self._planner.build_plan(now)
        return self._reasoner.plan(plan).text
