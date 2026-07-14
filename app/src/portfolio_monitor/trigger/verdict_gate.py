"""Verdict Gate (CLAUDE.md §4 / §5.2).

Mapea cada veredicto a su regla de trigger: qué dirección de movimiento lo
dispara y qué acción sugerir.

- `Crecer` / `Mantener`      → cae ≥ umbral  → sugerir **compra** en el dip.
- `Trim - tomar ganancias`   → sube ≥ umbral → sugerir **tomar ganancias** (reducir).
- `Consolidar`               → sube ≥ umbral → sugerir **consolidar / rotar**.

El resto (`Mantener - no sumar`, `Objetivo (sin comprar)`, `Migrar a UCITS`,
None) no dispara nada.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerRule:
    """Cómo dispara un veredicto: dirección del movimiento + acción sugerida."""

    direction: str  # "drop" | "rise"
    action: str     # "comprar_dip" | "tomar_ganancias" | "consolidar"


# Veredicto → regla. Los veredictos que no están acá no disparan ninguna alerta.
_RULES: dict[str, TriggerRule] = {
    "Crecer": TriggerRule("drop", "comprar_dip"),
    "Mantener": TriggerRule("drop", "comprar_dip"),
    "Trim - tomar ganancias": TriggerRule("rise", "tomar_ganancias"),
    "Consolidar": TriggerRule("rise", "consolidar"),
}


def trigger_rule(verdict: str | None) -> TriggerRule | None:
    """Regla de trigger del veredicto, o None si el veredicto no es accionable."""
    return _RULES.get(verdict or "")


def allows_buy(verdict: str | None) -> bool:
    """True solo si el veredicto habilita sugerir compra en un dip (§4)."""
    rule = trigger_rule(verdict)
    return rule is not None and rule.action == "comprar_dip"
