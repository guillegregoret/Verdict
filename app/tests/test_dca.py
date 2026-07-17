"""Tests del DcaSizer con fakes (sin DB)."""

from __future__ import annotations

import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import DcaPlan
from portfolio_monitor.dca import DcaSizer


class FakePlans:
    def __init__(self, plans: dict[str, DcaPlan] | None = None) -> None:
        self._plans = plans or {}

    def plans_by_ticker(self) -> dict[str, DcaPlan]:
        return dict(self._plans)


class FakeCash:
    def __init__(self, available: dict[str, float]) -> None:
        self._available = available

    def latest_available(self) -> dict[str, float]:
        return dict(self._available)


class FakeAccounts:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def account_by_ticker(self) -> dict[str, str]:
        return dict(self._mapping)


def _sizer(available=None, accounts=None, plans=None, **over) -> DcaSizer:
    return DcaSizer(
        plans=FakePlans(plans),
        cash=FakeCash({"U1": 1000.0} if available is None else available),
        accounts=FakeAccounts({"NVDA": "U1"} if accounts is None else accounts),
        settings=Settings(_env_file=None, **over),
    )


def test_rise_returns_none() -> None:
    assert _sizer().size("NVDA", 3.0) is None


def test_dip_scales_with_depth() -> None:
    # -3%: mult = 1 + 3*0.1 = 1.3 → 100 * 1.3 = 130 (hay cash de sobra)
    s = _sizer().size("NVDA", -3.0)
    assert s is not None
    assert s.multiplier == pytest.approx(1.3)
    assert s.amount_usd == pytest.approx(130.0)
    assert s.available_cash == 1000.0


def test_multiplier_is_capped() -> None:
    # -30%: 1 + 30*0.1 = 4 → topeado a max_multiplier 2.0 → 200
    s = _sizer().size("NVDA", -30.0)
    assert s is not None
    assert s.multiplier == 2.0
    assert s.amount_usd == pytest.approx(200.0)


def test_amount_capped_by_available_cash() -> None:
    # cash de la cuenta = 50 → aunque el tranche pida más, se capa a 50
    s = _sizer(available={"U1": 50.0}).size("NVDA", -3.0)
    assert s is not None
    assert s.amount_usd == 50.0


def test_no_cash_gives_zero() -> None:
    s = _sizer(available={}).size("NVDA", -3.0)
    assert s is not None
    assert s.amount_usd == 0.0
    assert s.available_cash == 0.0


def test_per_ticker_plan_overrides_defaults() -> None:
    plans = {"NVDA": DcaPlan(ticker="NVDA", tranche_usd=200.0, max_multiplier=1.5)}
    # -30%: mult topeado a 1.5 → 200*1.5 = 300 (cash 1000)
    s = _sizer(plans=plans).size("NVDA", -30.0)
    assert s is not None
    assert s.tranche_usd == 200.0
    assert s.multiplier == 1.5
    assert s.amount_usd == pytest.approx(300.0)
