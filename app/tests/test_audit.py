"""Tests de la auditoría de veredictos (verdict vs fundamentals)."""

from __future__ import annotations

from datetime import UTC, datetime

from portfolio_monitor.audit import audit_verdict
from portfolio_monitor.db.repositories import FundamentalsRow

TS = datetime(2026, 7, 29, tzinfo=UTC)


def _f(pe: float | None, growth: float | None) -> FundamentalsRow:
    return FundamentalsRow(
        ticker="X", ts=TS, pe=pe, revenue_growth=growth,
        gross_margin=0.6, debt_to_equity=0.2,
    )


def test_flags_trim_on_cheap_and_growing() -> None:
    # el caso MU: Trim con P/E 17 y +167% de ingresos → contradicción.
    a = audit_verdict("MU", "Trim - tomar ganancias", _f(17.4, 1.67))
    assert a is not None
    assert a.ticker == "MU"
    assert "P/E 17" in a.issue and "recorte" in a.issue


def test_does_not_flag_consolidar_when_expensive() -> None:
    # MRVL: Consolidar con P/E 61 → caro, la consolidación por valuación es válida.
    assert audit_verdict("MRVL", "Consolidar", _f(61.3, 0.34)) is None


def test_does_not_flag_consolidar_when_growth_weak() -> None:
    # QCOM: Consolidar con P/E 17 pero crecimiento +5% → no es "fuerte".
    assert audit_verdict("QCOM", "Consolidar", _f(17.0, 0.05)) is None


def test_flags_buy_when_revenue_shrinking() -> None:
    # un veredicto de compra sobre ingresos cayendo → revisar tesis.
    a = audit_verdict("ETN", "Mantener", _f(35.0, -0.15))
    assert a is not None
    assert "ingresos cayendo" in a.issue


def test_no_add_verdict_is_not_treated_as_buy() -> None:
    # 'Mantener - no sumar' NO es de compra: no se audita como tal.
    assert audit_verdict("CEG", "Mantener - no sumar", _f(24.0, -0.10)) is None


def test_healthy_buy_is_not_flagged() -> None:
    assert audit_verdict("NVDA", "Mantener", _f(30.0, 0.71)) is None


def test_missing_fundamentals_returns_none() -> None:
    assert audit_verdict("X", "Trim - tomar ganancias", None) is None


def test_missing_metrics_returns_none() -> None:
    # sin P/E o sin crecimiento no se puede afirmar contradicción de recorte.
    assert audit_verdict("X", "Trim - tomar ganancias", _f(None, 1.67)) is None
    assert audit_verdict("X", "Trim - tomar ganancias", _f(17.0, None)) is None
