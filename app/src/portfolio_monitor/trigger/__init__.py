"""Trigger Engine + Verdict Gate (§5, §11.4).

Detección de movimiento ≥ umbral → Verdict Gate (§4) → cooldown. Según el
veredicto dispara por caída (Crecer/Mantener → comprar) o por suba (Trim →
tomar ganancias, Consolidar → consolidar). Devuelve eventos accionables; no notifica.
"""

from .engine import TriggerEngine, TriggerEvent
from .verdict_gate import TriggerRule, allows_buy, trigger_rule

__all__ = ["TriggerEngine", "TriggerEvent", "TriggerRule", "allows_buy", "trigger_rule"]
