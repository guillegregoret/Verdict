"""ReasoningService: orquesta el reasoner primario con fallback (§11.6).

Si el primario (Anthropic) falla, cae al fallback (template) para que la alerta
igual salga. El notifier (§11.7) consume la Suggestion resultante.
"""

from __future__ import annotations

from ..logging import get_logger
from .models import ReasoningContext, Suggestion
from .reasoners import Reasoner, ReasoningError

logger = get_logger(__name__)


class ReasoningService:
    """Genera una sugerencia usando un reasoner primario con fallback opcional."""

    def __init__(self, primary: Reasoner, fallback: Reasoner | None = None) -> None:
        self._primary = primary
        self._fallback = fallback

    def suggest(self, context: ReasoningContext) -> Suggestion:
        """Devuelve una Suggestion; usa el fallback si el primario falla."""
        try:
            return self._primary.generate(context)
        except ReasoningError as exc:
            if self._fallback is None:
                raise
            logger.warning(
                "Reasoner primario falló para %s (%s); usando fallback.",
                context.ticker,
                exc,
            )
            return self._fallback.generate(context)
