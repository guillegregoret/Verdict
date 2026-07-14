"""Tests del Verdict Gate (§4/§5.2)."""

from __future__ import annotations

import pytest

from portfolio_monitor.trigger import allows_buy, trigger_rule


@pytest.mark.parametrize(
    "verdict, expected",
    [
        ("Crecer", True),
        ("Mantener", True),
        ("Mantener - no sumar", False),
        ("Trim - tomar ganancias", False),
        ("Consolidar", False),
        ("Objetivo (sin comprar)", False),
        ("Migrar a UCITS", False),
        (None, False),
        ("cualquier cosa", False),
    ],
)
def test_allows_buy(verdict: str | None, expected: bool) -> None:
    assert allows_buy(verdict) is expected


@pytest.mark.parametrize(
    "verdict, direction, action",
    [
        ("Crecer", "drop", "comprar_dip"),
        ("Mantener", "drop", "comprar_dip"),
        ("Trim - tomar ganancias", "rise", "tomar_ganancias"),
        ("Consolidar", "rise", "consolidar"),
    ],
)
def test_trigger_rule_actionable(verdict: str, direction: str, action: str) -> None:
    rule = trigger_rule(verdict)
    assert rule is not None
    assert rule.direction == direction
    assert rule.action == action


@pytest.mark.parametrize(
    "verdict",
    ["Mantener - no sumar", "Objetivo (sin comprar)", "Migrar a UCITS", None, "x"],
)
def test_trigger_rule_non_actionable(verdict: str | None) -> None:
    assert trigger_rule(verdict) is None
