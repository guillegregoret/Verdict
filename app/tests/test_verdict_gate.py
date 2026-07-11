"""Tests del Verdict Gate (§4/§5.2)."""

from __future__ import annotations

import pytest

from portfolio_monitor.trigger import allows_buy


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
