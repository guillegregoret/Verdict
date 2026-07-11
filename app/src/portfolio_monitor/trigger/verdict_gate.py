"""Verdict Gate (CLAUDE.md §4 / §5.2).

Decide qué veredictos habilitan una **sugerencia de compra** en un dip.
Solo `Crecer` y `Mantener` pasan. El resto (`Mantener - no sumar`, `Trim - tomar
ganancias`, `Consolidar`, `Objetivo (sin comprar)`, `Migrar a UCITS`) NO sugieren
compra — a lo sumo serían informativos (feature futura).
"""

from __future__ import annotations

BUY_VERDICTS = frozenset({"Crecer", "Mantener"})


def allows_buy(verdict: str | None) -> bool:
    """True solo si el veredicto habilita sugerir compra en un dip."""
    return verdict in BUY_VERDICTS
