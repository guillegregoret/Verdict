"""Trigger Engine + Verdict Gate (§5, §11.4).

Detección de caída ≥ umbral → Verdict Gate (§4) → cooldown. Solo Crecer/Mantener
generan sugerencia de compra. Devuelve eventos accionables; no notifica.
"""

from .engine import TriggerEngine, TriggerEvent
from .verdict_gate import allows_buy

__all__ = ["TriggerEngine", "TriggerEvent", "allows_buy"]
