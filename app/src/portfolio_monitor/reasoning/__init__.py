"""Reasoning: arma el contexto del TriggerEvent + fundamentals → sugerencia (§11.6).

Primario Anthropic (Claude) con fallback a un template determinístico. No notifica:
devuelve una Suggestion que consume el notifier (§11.7).
"""

from .models import MonitorSignal, ReasoningContext, Suggestion
from .reasoners import AnthropicReasoner, Reasoner, ReasoningError, TemplateReasoner
from .service import ReasoningService

__all__ = [
    "AnthropicReasoner",
    "MonitorSignal",
    "Reasoner",
    "ReasoningContext",
    "ReasoningError",
    "ReasoningService",
    "Suggestion",
    "TemplateReasoner",
]
