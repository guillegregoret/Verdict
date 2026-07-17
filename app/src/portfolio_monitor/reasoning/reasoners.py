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
    "ejecute manualmente en su broker. La señal puede ser: sumar en una caída, "
    "tomar ganancias / consolidar en una suba, o un DETERIORO de fundamentals "
    "(la tesis se estaría rompiendo → revisar/reducir). Respondé en español "
    "rioplatense, en 2 a 4 oraciones, concreto y accionable. Si el contexto trae "
    "un DCA sugerido, mencioná el monto y que está topeado al cash disponible. "
    "No inventes datos que no estén en el contexto; si faltan fundamentals, decilo."
)

# Etiqueta legible de la acción a evaluar (deriva del veredicto / la señal).
_ACTION_LABELS = {
    "comprar_dip": "evaluar SUMAR en la caída",
    "tomar_ganancias": "evaluar TOMAR GANANCIAS (reducir la posición)",
    "consolidar": "evaluar CONSOLIDAR / rotar la posición",
    "revisar_tesis": "REVISAR LA TESIS (fundamentals deteriorados)",
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


def _format_dca(context: ReasoningContext) -> str:
    """Línea de DCA/cash (§5.4), o vacío si no aplica."""
    if context.dca_suggested_usd is not None:
        cash = (
            f" (cash disponible ${context.bucket_remaining:.0f})"
            if context.bucket_remaining is not None
            else ""
        )
        return f"DCA sugerido: comprar ~${context.dca_suggested_usd:.0f}{cash}"
    if context.bucket_remaining is not None:
        return f"Cash disponible: ${context.bucket_remaining:.0f}"
    return ""


def _action_label(context: ReasoningContext) -> str:
    return _ACTION_LABELS.get(context.action, context.action)


def _build_context_block(context: ReasoningContext) -> str:
    """Bloque de contexto que se le pasa al modelo (según el tipo de señal)."""
    if context.signal_kind == "fundamentals_decay":
        return "\n".join([
            f"Ticker: {context.ticker}",
            f"Veredicto configurado: {context.verdict}",
            "Señal: DETERIORO de fundamentals (la tesis podría estar rompiéndose).",
            f"Qué empeoró: {context.note}",
            f"Acción a evaluar: {_action_label(context)}",
            _format_fundamentals(context),
        ])
    lines = [
        f"Ticker: {context.ticker}",
        f"Movimiento: {context.pct_change:+.2f}% en una ventana de "
        f"{context.window_minutes} minutos",
        f"Precio: {context.current_price:.2f} (referencia {context.reference_price:.2f})",
        f"Veredicto configurado: {context.verdict}",
        f"Acción a evaluar: {_action_label(context)}",
        _format_fundamentals(context),
    ]
    lines.append(_format_dca(context))
    return "\n".join(line for line in lines if line)


class TemplateReasoner:
    """Sugerencia determinística sin llamar a ninguna API (fallback / MVP)."""

    def generate(self, context: ReasoningContext) -> Suggestion:
        if context.signal_kind == "fundamentals_decay":
            text = (
                f"⚠️ {context.ticker} ({context.verdict}): fundamentals deteriorados "
                f"— {context.note}. Revisá la tesis."
            )
            return Suggestion(text=text, source="template")

        arrow = "📉" if context.pct_change < 0 else "📈"
        header = (
            f"{arrow} {context.ticker} {context.pct_change:+.1f}% "
            f"(ventana {context.window_minutes}m). Veredicto: {context.verdict}. "
            f"Acción: {_action_label(context)}."
        )
        body = _format_fundamentals(context)
        dca = _format_dca(context)
        if dca:
            body += f" {dca}."
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
