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
from .models import PortfolioReviewContext, ReasoningContext, Suggestion

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "Sos un asistente de análisis de inversiones READ-ONLY. NUNCA ejecutás "
    "órdenes: solo preparás una sugerencia breve para que el usuario decida y "
    "ejecute manualmente en su broker. La señal puede ser: sumar en una caída, "
    "tomar ganancias / consolidar en una suba, un DETERIORO de fundamentals, un "
    "CAMBIO en el consenso de analistas, o un REPORTE de earnings. En todos los "
    "casos VERIFICÁ LA TESIS: cruzá el evento (movimiento, sorpresa de EPS, "
    "cambio de rating) contra los fundamentals del contexto (P/E, crecimiento de "
    "ingresos, margen, deuda) y el veredicto configurado, y decí si la tesis "
    "sigue en pie o conviene revisarla. Respondé en español rioplatense, en 2 a 4 "
    "oraciones, concreto y accionable. Si el contexto trae un DCA sugerido, "
    "mencioná el monto y que está topeado al cash disponible. No inventes datos "
    "que no estén en el contexto; si faltan fundamentals, decilo."
)

_REVIEW_SYSTEM_PROMPT = (
    "Sos un asistente de análisis de cartera READ-ONLY. NUNCA ejecutás órdenes: "
    "el usuario decide y ejecuta manualmente en su broker. Te paso el portfolio "
    "COMPLETO: cada posición con su peso %, veredicto configurado y fundamentals, "
    "más el cash por cuenta. Hacé una REEVALUACIÓN INTEGRAL:\n"
    "1) Tesis por posición: en una línea por ticker, decí si la tesis SIGUE EN PIE "
    "según sus fundamentals y el veredicto, o si conviene revisarla.\n"
    "2) Concentración: señalá pesos altos o desbalances relevantes.\n"
    "3) Ideas de COMPRA (posiciones con tesis intacta para sumar en dips) y de "
    "VENTA/TRIM. Respetá el veredicto: 'Mantener - no sumar' NO se suma, 'Trim' se "
    "reduce, 'Consolidar' se rota; 'Crecer'/'Mantener' son candidatos de compra.\n"
    "4) Cash: qué hacer con el disponible por cuenta.\n"
    "Español rioplatense, conciso pero completo, con bullets y el ticker al inicio "
    "de cada línea. No inventes datos fuera del contexto; si a un ticker le faltan "
    "fundamentals, decilo."
)

# Etiqueta legible de la acción a evaluar (deriva del veredicto / la señal).
_ACTION_LABELS = {
    "comprar_dip": "evaluar SUMAR en la caída",
    "tomar_ganancias": "evaluar TOMAR GANANCIAS (reducir la posición)",
    "consolidar": "evaluar CONSOLIDAR / rotar la posición",
    "revisar_tesis": "REVISAR la tesis",
}


class ReasoningError(RuntimeError):
    """Error al generar una sugerencia."""


class Reasoner(Protocol):
    def generate(self, context: ReasoningContext) -> Suggestion: ...
    def review(self, context: PortfolioReviewContext) -> Suggestion: ...


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


def _review_user_prompt(context: PortfolioReviewContext) -> str:
    """Prompt de usuario para la reevaluación integral (/reevaluar)."""
    lines = [
        "Reevaluá esta cartera completa:",
        "",
        f"Resumen: {context.position_count} posiciones · valor de mercado "
        f"${context.total_value:,.0f} · cash disponible ${context.total_cash:,.0f}.",
    ]
    if context.note:
        lines.append(f"Concentración: {context.note}")
    lines += ["", "Posiciones (peso · veredicto · fundamentals):", context.positions_block]
    if context.cash_block:
        lines += ["", "Cash por cuenta:", context.cash_block]
    return "\n".join(lines)


_SIGNAL_HEADERS = {
    "fundamentals_decay": "DETERIORO de fundamentals (la tesis podría estar rompiéndose).",
    "rating_shift": "CAMBIO en el consenso de analistas.",
    "post_earnings": "REPORTÓ earnings (reacción del mercado).",
}


def _build_context_block(context: ReasoningContext) -> str:
    """Bloque de contexto que se le pasa al modelo (según el tipo de señal)."""
    if context.signal_kind in _SIGNAL_HEADERS:
        return "\n".join([
            f"Ticker: {context.ticker}",
            f"Veredicto configurado: {context.verdict}",
            f"Señal: {_SIGNAL_HEADERS[context.signal_kind]}",
            f"Detalle: {context.note}",
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
        if context.signal_kind in _SIGNAL_HEADERS:
            label = {
                "fundamentals_decay": ("⚠️", "fundamentals deteriorados"),
                "rating_shift": ("⚠️", "cambio de rating"),
                "post_earnings": ("📊", "reporte de earnings"),
            }[context.signal_kind]
            text = (
                f"{label[0]} {context.ticker} ({context.verdict}): {label[1]} — "
                f"{context.note}. Revisá la tesis."
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

    def review(self, context: PortfolioReviewContext) -> Suggestion:
        """Reevaluación básica sin Claude: vuelca posiciones + cash tal cual."""
        lines = [
            f"📊 Reevaluación del portfolio — {context.position_count} posiciones · "
            f"${context.total_value:,.0f} · cash ${context.total_cash:,.0f}",
        ]
        if context.note:
            lines.append(f"⚠️ {context.note}")
        lines += ["", context.positions_block]
        if context.cash_block:
            lines += ["", "Cash por cuenta:", context.cash_block]
        lines.append("\n(Sin Claude disponible: revisión automática básica.)")
        return Suggestion(text="\n".join(lines), source="template")


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

    def review(self, context: PortfolioReviewContext) -> Suggestion:
        """Reevaluación integral del portfolio con Claude (/reevaluar)."""
        try:
            resp = self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=2000,
                system=_REVIEW_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _review_user_prompt(context)}],
            )
        except Exception as exc:  # noqa: BLE001 - normalizamos errores del SDK
            raise ReasoningError(f"Fallo llamando a Anthropic: {exc}") from exc

        if getattr(resp, "stop_reason", None) == "refusal":
            raise ReasoningError("Anthropic rechazó la reevaluación del portfolio.")

        text = "".join(
            block.text
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise ReasoningError("Respuesta vacía de Anthropic.")
        return Suggestion(text=text, source="anthropic")
