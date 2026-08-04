"""Tests del planificador de despliegue de cash (parte determinística)."""

from __future__ import annotations

from datetime import UTC, datetime

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import (
    AccountCashRow,
    DcaPlan,
    FundamentalsRow,
    HoldingPosition,
    TickerConfig,
)
from portfolio_monitor.planning import CashPlanService, PlanService, format_plan

NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)


class FakeHoldings:
    def __init__(self, rows: list[HoldingPosition]) -> None:
        self._rows = rows

    def positions(self) -> list[HoldingPosition]:
        return list(self._rows)


class FakePrices:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def latest_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)


class FakeCash:
    def __init__(self, rows: list[AccountCashRow]) -> None:
        self._rows = rows

    def latest(self) -> list[AccountCashRow]:
        return list(self._rows)


class FakeFund:
    def __init__(self, pes: dict[str, float]) -> None:
        self._pes = pes

    def latest(self, ticker: str) -> FundamentalsRow | None:
        if ticker not in self._pes:
            return None
        return FundamentalsRow(ticker, NOW, self._pes[ticker], 0.2, 0.6, 0.3)


class FakeDca:
    def __init__(self, plans: dict[str, DcaPlan] | None = None) -> None:
        self._plans = plans or {}

    def plans_by_ticker(self) -> dict[str, DcaPlan]:
        return dict(self._plans)


class FakeConfigs:
    def __init__(self, thresholds: dict[str, float]) -> None:
        self._t = thresholds

    def enabled_configs(self) -> list[TickerConfig]:
        return [TickerConfig(t, v, 390, 4.0) for t, v in self._t.items()]


def _pos(ticker, acct, shares, cost, verdict, target, cluster="Compute/GPU"):
    return HoldingPosition("U1" if acct == "IA" else "U2", acct, ticker, shares,
                           cost, verdict, target, cluster)


def _service(positions, prices, cash, pes=None, dca=None, thresholds=None) -> CashPlanService:
    return CashPlanService(
        holdings=FakeHoldings(positions),
        prices=FakePrices(prices),
        cash=FakeCash(cash),
        fundamentals=FakeFund(pes or {}),
        dca=FakeDca(dca),
        configs=FakeConfigs(thresholds or {}),
        settings=Settings(_env_file=None),
    )


def _cash(ibkr, name, avail):
    return AccountCashRow(ibkr, name, avail, avail, "USD")


def test_gap_driven_allocation_respects_targets() -> None:
    # NVDA 66.7% (target 50 → sobre target, gap 0); GOOG 33.3% (target 50 → gap 250).
    positions = [
        _pos("NVDA", "IA", 10, 90, "Mantener", 50.0),
        _pos("GOOG", "IA", 10, 40, "Crecer", 50.0),
    ]
    plan = _service(
        positions, {"NVDA": 100.0, "GOOG": 50.0}, [_cash("U1", "IA", 1000.0)]
    ).build_plan(NOW)
    assert plan.targets_set is True
    acct = plan.accounts[0]
    assert acct.reserve_usd == 200.0          # max(15%*1000=150, floor 200)
    assert acct.deployable_usd == 800.0
    by_t = {c.ticker: c for c in acct.candidates}
    assert by_t["GOOG"].gap_usd == 250.0
    assert by_t["GOOG"].suggested_usd == 250.0  # gap total 250 ≤ 800 → full
    assert by_t["NVDA"].suggested_usd == 0.0    # sobre target


def test_no_target_signal_does_not_invent_allocation() -> None:
    # targets = peso actual → sin señal: suggested 0, nota, orden por P/E.
    positions = [
        _pos("NVDA", "IA", 10, 90, "Mantener", 66.7),
        _pos("GOOG", "IA", 10, 40, "Crecer", 33.3),
    ]
    plan = _service(
        positions, {"NVDA": 100.0, "GOOG": 50.0}, [_cash("U1", "IA", 1000.0)],
        pes={"NVDA": 30.0, "GOOG": 18.0},
    ).build_plan(NOW)
    assert plan.targets_set is False
    acct = plan.accounts[0]
    assert all(c.suggested_usd == 0.0 for c in acct.candidates)
    assert acct.note is not None and "sin señal" in acct.note
    assert acct.candidates[0].ticker == "GOOG"  # P/E 18 < 30 → primero


def test_only_buy_verdicts_are_candidates() -> None:
    positions = [
        _pos("NVDA", "IA", 10, 90, "Mantener", 40.0),
        _pos("MU", "IA", 1, 700, "Trim - tomar ganancias", 10.0),
        _pos("CEG", "IA", 2, 300, "Mantener - no sumar", 10.0),
        _pos("MRVL", "IA", 2, 250, "Consolidar", 10.0),
    ]
    plan = _service(
        positions, {"NVDA": 100.0, "MU": 700.0, "CEG": 300.0, "MRVL": 250.0},
        [_cash("U1", "IA", 1000.0)],
    ).build_plan(NOW)
    tickers = {c.ticker for c in plan.accounts[0].candidates}
    assert tickers == {"NVDA"}  # solo el veredicto de compra


def test_cash_does_not_cross_accounts() -> None:
    # cash solo en IA; una posición de compra en Salud no debe recibir nada.
    positions = [
        _pos("NVDA", "IA", 10, 90, "Mantener", 40.0),
        _pos("LLY", "Salud", 1, 1000, "Crecer", 40.0, cluster="Salud"),
    ]
    plan = _service(
        positions, {"NVDA": 100.0, "LLY": 1200.0}, [_cash("U1", "IA", 1000.0)]
    ).build_plan(NOW)
    assert [a.ibkr_id for a in plan.accounts] == ["U1"]  # solo la cuenta con cash
    tickers = {c.ticker for a in plan.accounts for c in a.candidates}
    assert "LLY" not in tickers


def test_reserve_floor_applies_on_small_cash() -> None:
    plan = _service(
        [_pos("NVDA", "IA", 10, 90, "Mantener", 40.0)],
        {"NVDA": 100.0}, [_cash("U1", "IA", 150.0)],
    ).build_plan(NOW)
    acct = plan.accounts[0]
    assert acct.reserve_usd == 150.0    # floor 200 > cash 150 → toda la reserva
    assert acct.deployable_usd == 0.0   # nada desplegable


def test_tranches_property() -> None:
    positions = [
        _pos("NVDA", "IA", 10, 90, "Mantener", 30.0),
        _pos("GOOG", "IA", 10, 40, "Crecer", 90.0),
    ]
    plan = _service(
        positions, {"NVDA": 100.0, "GOOG": 50.0}, [_cash("U1", "IA", 2000.0)],
        dca={"GOOG": DcaPlan("GOOG", 200.0, 2.0)},
    ).build_plan(NOW)
    goog = [c for c in plan.accounts[0].candidates if c.ticker == "GOOG"][0]
    assert goog.suggested_usd > 0
    assert goog.tranche_usd == 200.0
    assert goog.tranches == round(goog.suggested_usd / 200.0)


def test_format_plan_renders_concrete_numbers() -> None:
    positions = [_pos("GOOG", "IA", 10, 40, "Crecer", 90.0)]
    plan = _service(
        positions, {"GOOG": 50.0}, [_cash("U1", "IA", 1000.0)]
    ).build_plan(NOW)
    txt = format_plan(plan)
    assert "Plan de despliegue de cash" in txt
    assert "GOOG" in txt
    assert "Read-only" in txt


class FakeReasoner:
    def __init__(self) -> None:
        self.plan_arg = None

    def plan(self, plan):  # noqa: ANN001
        self.plan_arg = plan
        from portfolio_monitor.reasoning import Suggestion
        return Suggestion(text="PRIORIZADO", source="template")


def test_plan_service_pipes_to_reasoner() -> None:
    r = FakeReasoner()
    svc = PlanService(
        _service([_pos("NVDA", "IA", 10, 90, "Mantener", 40.0)],
                 {"NVDA": 100.0}, [_cash("U1", "IA", 1000.0)]),
        r,
    )
    out = svc.deliver(NOW)
    assert out == "PRIORIZADO"
    assert r.plan_arg is not None  # recibió el DeploymentPlan
