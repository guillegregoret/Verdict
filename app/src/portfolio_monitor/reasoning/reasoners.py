"""Reasoners: generan la sugerencia a partir del contexto (§5.6, §11.6).

- `AnthropicReasoner`: usa el SDK de Anthropic (Claude). Import perezoso: el
  resto del código y los tests no requieren la dependencia si inyectan un client.
- `TemplateReasoner`: sugerencia determinística sin API. Sirve de fallback y de
  primer corte del MVP.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..config import Settings
from ..logging import get_logger
from .models import ReasoningContext, Suggestion

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "Sos un asistente de análisis de inversiones READ-ONLY. NUNCA ejecutás "
    "órdenes: solo preparás una sugerencia breve para que el usuario decida y "
    "ejecute manualmente en su broker. Según el veredicto del activo, la señal "
    "puede ser para sumar en una caída, o para tomar ganancias / consolidar en "
    "una suba. Respondé en español rioplatense, en 2 a 4 oraciones, concreto y "
    "accionable. No inventes datos que no estén en el contexto; si faltan "
    "fundamentals, decilo."
)

# Etiqueta legible de la acción a evaluar (deriva del veredicto en el gate).
_ACTION_LABELS = {
    "comprar_dip": "evaluar SUMAR en la caída",
    "tomar_ganancias": "evaluar TOMAR GANANCIAS (reducir la posición)",
    "consolidar": "evaluar CONSOLIDAR / rotar la posición",
}


class ReasoningError(RuntimeError):
    """Error al generar una sugerencia."""


class Reasoner(Protocol):
    def generate(self, context: ReasoningContext) -> Suggestion: ...


def _format_fundamentals(context: ReasoningContext) -> str:
    """Resumen legible de los fundamentals (o aviso de que faltan)."""
    f = context.fundamentals
    if f is None:
        return "Fundamentals: no disponibles."
    parts: list[str] = []
    if f.pe is not None:
        parts.append(f"P/E {f.pe:.1f}")
    if f.revenue_growth is not None:
        parts.append(f"crecimiento de ingresos {f.revenue_growth * 100:+.1f}%")
    if f.gross_margin is not None:
        parts.append(f"margen bruto {f.gross_margin * 100:.1f}%")
    if f.debt_to_equity is not None:
        parts.append(f"deuda/equity {f.debt_to_equity:.2f}")
    return "Fundamentals: " + (", ".join(parts) if parts else "sin métricas.")


def _action_label(context: ReasoningContext) -> str:
    return _ACTION_LABELS.get(context.action, context.action)


def _build_context_block(context: ReasoningContext) -> str:
    """Bloque de contexto que se le pasa al modelo."""
    lines = [
        f"Ticker: {context.ticker}",
        f"Movimiento: {context.pct_change:+.2f}% en una ventana de "
        f"{context.window_minutes} minutos",
        f"Precio: {context.current_price:.2f} (referencia {context.reference_price:.2f})",
        f"Veredicto configurado: {context.verdict}",
        f"Acción a evaluar: {_action_label(context)}",
        _format_fundamentals(context),
    ]
    if context.bucket_remaining is not None:
        lines.append(f"Bucket de compra restante: {context.bucket_remaining:.2f}")
    return "\n".join(lines)


class TemplateReasoner:
    """Sugerencia determinística sin llamar a ninguna API (fallback / MVP)."""

    def generate(self, context: ReasoningContext) -> Suggestion:
        arrow = "📉" if context.pct_change < 0 else "📈"
        header = (
            f"{arrow} {context.ticker} {context.pct_change:+.1f}% "
            f"(ventana {context.window_minutes}m). Veredicto: {context.verdict}. "
            f"Acción: {_action_label(context)}."
        )
        body = _format_fundamentals(context)
        if context.bucket_remaining is not None:
            body += f" Bucket restante: {context.bucket_remaining:.2f}."
        return Suggestion(text=f"{header} {body}", source="template")


class AnthropicReasoner:
    """Genera la sugerencia con Claude (Anthropic Messages API)."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        if client is not None:
            self._client = client
        else:  # import perezoso: solo en uso real
            if not settings.anthropic_api_key:
                raise ReasoningError("ANTHROPIC_API_KEY no configurada.")
            from anthropic import Anthropic  # noqa: PLC0415

            self._client = Anthropic(api_key=settings.anthropic_api_key)

    def generate(self, context: ReasoningContext) -> Suggestion:
        user_prompt = (
            "Analizá esta señal y sugerí qué hacer:\n\n"
            + _build_context_block(context)
        )
        try:
            resp = self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=400,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - normalizamos errores del SDK
            raise ReasoningError(f"Fallo llamando a Anthropic: {exc}") from exc

        if getattr(resp, "stop_reason", None) == "refusal":
            raise ReasoningError(
                f"Anthropic rechazó la sugerencia para {context.ticker}."
            )

        text = "".join(
            block.text
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise ReasoningError("Respuesta vacía de Anthropic.")
        return Suggestion(text=text, source="anthropic")
