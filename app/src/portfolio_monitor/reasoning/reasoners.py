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
from ..planning.format import format_plan
from ..planning.models import DeploymentPlan
from .context_docs import load_context_docs
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
    "que no estén en el contexto; si faltan fundamentals, decilo.\n"
    "CRÍTICO — no confundas el movimiento del mercado con la ganancia del "
    "usuario: el % de movimiento es de la ventana, mientras que 'Mi posición' "
    "dice cómo viene el usuario contra su costo promedio. NUNCA hables de "
    "'tomar ganancias' si la posición está EN PÉRDIDA; en ese caso, si el "
    "veredicto es Trim/Consolidar, encuadralo como usar el rebote para salir "
    "(reducir la pérdida / rotar), aclarando que se vende en rojo. Y si la "
    "posición está en ganancia fuerte, decilo. Si no hay dato de posición, no "
    "asumas ni ganancia ni pérdida.\n"
    "CONTEXTO DE MERCADO — si viene, usalo para distinguir si el movimiento es "
    "del activo o del mercado: cuando los benchmarks caen parecido y muchas de "
    "mis posiciones están en rojo en la ventana, es una baja general (no un "
    "deterioro propio del activo) y conviene decirlo; si el activo cae MUCHO más "
    "que su índice/sector, el movimiento es idiosincrático y merece más cautela. "
    "No lo inventes si no está."
)

_REVIEW_SYSTEM_PROMPT = (
    "You are a READ-ONLY portfolio analysis assistant. You NEVER place orders: the "
    "user decides and executes manually in their broker. You receive the FULL "
    "portfolio: each position with its weight %, configured verdict, unrealized "
    "P&L vs the user's average cost, and fundamentals, plus cash per account. "
    "Produce a COMPREHENSIVE REVIEW in English, using Markdown:\n"
    "1) Thesis per position: one line per ticker stating whether the thesis STILL "
    "HOLDS given its fundamentals and verdict, or should be revisited.\n"
    "2) Concentration: flag heavy weights or relevant imbalances. When a position "
    "shows a target weight, use the drift (current − target, in pp): materially "
    "BELOW target with an intact thesis and a buy verdict is a candidate to add; "
    "materially ABOVE target is a candidate to trim toward target. Also read the "
    "'Exposure by cluster': single-ticker weights can look fine while a THEME "
    "(e.g. AI/semis/power) dominates the book — call out thematic concentration "
    "and correlated risk when one or a few clusters carry most of the portfolio.\n"
    "3) BUY ideas (positions with an intact thesis to add on dips) and SELL/TRIM "
    "ideas. Respect the verdict: 'Mantener - no sumar' is NOT added to, 'Trim' is "
    "reduced, 'Consolidar' is rotated out; 'Crecer'/'Mantener' are buy candidates.\n"
    "4) Cash: what to do with the available cash per account.\n"
    "Be concise but complete, use bullets, and start each position line with the "
    "ticker. Do not invent data beyond the context; if a ticker is missing "
    "fundamentals, say so. Keep the configured verdict labels verbatim.\n"
    "CRITICAL: never describe trimming a LOSING position as 'taking profits'. "
    "Use the P&L sign: a position in the red that must be reduced (Trim / "
    "Consolidar) is exiting at a loss — say so plainly, and note the size of the "
    "loss. Positions deep in the red with an intact thesis and a buy verdict may "
    "be averaging-down candidates; positions deep in the red whose thesis broke "
    "should be called out as such.\n"
    "VERDICT AUDIT: if the context lists a 'Verdict audit', those configured "
    "verdicts contradict the fundamentals — the label is likely stale. Call it "
    "out explicitly and suggest the user reconsider the verdict (e.g. a 'Trim' on "
    "a cheap, fast-growing name whose thesis is intact is probably not a trim). "
    "Do NOT blindly follow a verdict the audit flagged."
)

_PLAN_SYSTEM_PROMPT = (
    "Sos un estratega READ-ONLY de despliegue de cash. NUNCA ejecutás órdenes: "
    "preparás una recomendación para que el usuario decida y ejecute en su broker. "
    "Recibís un PLAN DETERMINÍSTICO ya calculado: cash por cuenta (el cash NO cruza "
    "cuentas), y candidatos con veredicto de compra (peso actual, target, gap-a-"
    "target en $, valuación P/E, P&L, cluster y su peso, tramos y gatillo de caída). "
    "Tu tarea es PRIORIZAR y explicar, en español rioplatense, conciso y accionable:\n"
    "1) En qué orden desplegar y por qué (gap a target, valuación, tesis).\n"
    "2) CONCENTRACIÓN: si un cluster ya pesa mucho (ej: el complejo AI/semis/power), "
    "avisá y sugerí no agrandar esa apuesta; preferí lo infraponderado.\n"
    "3) Cuánto cash conviene RETENER como colchón (no desplegar todo de golpe).\n"
    "4) Si el plan dice que los targets = peso actual (sin señal de rebalanceo), "
    "decílo, priorizá por convicción (tesis + valuación + cluster infraponderado) y "
    "recomendá cargar targets reales para afinar.\n"
    "No inventes números fuera del plan. No es un consejo de inversión ni una orden: "
    "es soporte de decisión; el usuario ejecuta. Respetá los montos y las cuentas."
)

# Etiqueta legible de la acción a evaluar (deriva del veredicto / la señal).
_ACTION_LABELS = {
    "comprar_dip": "evaluar SUMAR en la caída",
    "tomar_ganancias": "evaluar TOMAR GANANCIAS (reducir la posición)",
    "consolidar": "evaluar CONSOLIDAR / rotar la posición",
    "revisar_tesis": "REVISAR la tesis",
    "watchlist_dip": "evaluar ENTRADA en la watchlist (target que seguís sin tener)",
}


class ReasoningError(RuntimeError):
    """Error al generar una sugerencia."""


class Reasoner(Protocol):
    def generate(self, context: ReasoningContext) -> Suggestion: ...
    def review(self, context: PortfolioReviewContext) -> Suggestion: ...
    def plan(self, plan: DeploymentPlan) -> Suggestion: ...


def _plan_user_prompt(plan: DeploymentPlan) -> str:
    """User prompt del planificador: el plan determinístico + pedido de prioridad."""
    return (
        "Priorizá este plan de despliegue de cash (qué desplegar primero, "
        "concentración a cuidar, cuánto retener):\n\n" + format_plan(plan)
    )


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


def _format_position(context: ReasoningContext) -> str:
    """P&L no realizado de la posición del usuario (o vacío si no hay dato).

    Es la línea que evita el error de leer una suba de mercado como ganancia
    propia: el movimiento es de la ventana, esto es contra el costo promedio.
    """
    pnl = context.unrealized_pct
    if pnl is None or context.avg_cost is None:
        return ""
    estado = "EN GANANCIA" if pnl >= 0 else "EN PÉRDIDA"
    return (
        f"Mi posición: {estado} {pnl:+.1f}% vs costo promedio "
        f"${context.avg_cost:.2f} (precio actual ${context.current_price:.2f})"
    )


def _format_market(context: ReasoningContext) -> str:
    """Línea de contexto de mercado (benchmarks + amplitud), o vacío si no hay."""
    if context.market is None:
        return ""
    summary = context.market.summary()
    return f"Contexto de mercado: {summary}" if summary else ""


def _action_label(context: ReasoningContext) -> str:
    return _ACTION_LABELS.get(context.action, context.action)


def _review_user_prompt(context: PortfolioReviewContext) -> str:
    """User prompt for the comprehensive portfolio review (/reevaluar)."""
    lines = [
        "Review this full portfolio:",
        "",
        f"Summary: {context.position_count} positions · market value "
        f"${context.total_value:,.0f} · available cash ${context.total_cash:,.0f}.",
    ]
    if context.note:
        lines.append(f"Concentration: {context.note}")
    if context.clusters:
        lines += ["", "Exposure by cluster (thematic concentration):"]
        for c in context.clusters:
            pnl = f", P&L {c.unrealized_pct:+.1f}%" if c.unrealized_pct is not None else ""
            lines.append(f"- {c.cluster}: {c.weight:.1f}% ({c.position_count} pos{pnl})")
    if context.audit_flags:
        lines += [
            "",
            "Verdict audit — configured verdicts that CONTRADICT the fundamentals "
            "(the label may be stale; weigh this):",
            *(f"- {flag}" for flag in context.audit_flags),
        ]
    lines += ["", "Positions (weight · verdict · fundamentals):", context.positions_block]
    if context.cash_block:
        lines += ["", "Cash per account:", context.cash_block]
    return "\n".join(lines)


_SIGNAL_HEADERS = {
    "fundamentals_decay": "DETERIORO de fundamentals (la tesis podría estar rompiéndose).",
    "rating_shift": "CAMBIO en el consenso de analistas.",
    "post_earnings": "REPORTÓ earnings (reacción del mercado).",
}


def _build_context_block(context: ReasoningContext) -> str:
    """Bloque de contexto que se le pasa al modelo (según el tipo de señal)."""
    if context.signal_kind in _SIGNAL_HEADERS:
        lines = [
            f"Ticker: {context.ticker}",
            f"Veredicto configurado: {context.verdict}",
            f"Señal: {_SIGNAL_HEADERS[context.signal_kind]}",
            f"Detalle: {context.note}",
            f"Acción a evaluar: {_action_label(context)}",
            _format_position(context),
            _format_market(context),
            _format_fundamentals(context),
        ]
        return "\n".join(line for line in lines if line)
    lines = [
        f"Ticker: {context.ticker}",
        f"Movimiento del papel: {context.pct_change:+.2f}% en una ventana de "
        f"{context.window_minutes} minutos",
        f"Precio: {context.current_price:.2f} (referencia {context.reference_price:.2f})",
        f"Veredicto configurado: {context.verdict}",
        _format_position(context),
        _format_market(context),
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
        position = _format_position(context)
        body = f"{position}. {_format_fundamentals(context)}" if position \
            else _format_fundamentals(context)
        market = _format_market(context)
        if market:
            body += f" {market}."
        dca = _format_dca(context)
        if dca:
            body += f" {dca}."
        return Suggestion(text=f"{header} {body}", source="template")

    def review(self, context: PortfolioReviewContext) -> Suggestion:
        """Basic review without Claude: dumps positions + cash as-is."""
        lines = [
            f"📊 Portfolio review — {context.position_count} positions · "
            f"${context.total_value:,.0f} · cash ${context.total_cash:,.0f}",
        ]
        if context.note:
            lines.append(f"⚠️ {context.note}")
        lines += ["", context.positions_block]
        if context.cash_block:
            lines += ["", "Cash per account:", context.cash_block]
        lines.append("\n(Claude unavailable: basic automated review.)")
        return Suggestion(text="\n".join(lines), source="template")

    def plan(self, plan: DeploymentPlan) -> Suggestion:
        """Sin Claude: devuelve el plan determinístico tal cual (ya es accionable)."""
        return Suggestion(text=format_plan(plan), source="template")


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

            self._client = Anthropic(
                api_key=settings.anthropic_api_key,
                max_retries=settings.anthropic_max_retries,
            )

    def _system(self, base: str) -> str:
        """System prompt + contexto extra del usuario (.md), leído por llamada.

        Se lee en cada consulta a propósito: así editás los .md sin rebuildear.
        Los archivos son locales y chicos, el costo de I/O es despreciable.
        """
        return base + load_context_docs(
            self._settings.strategy_context_dir,
            self._settings.strategy_context_max_chars,
        )

    def generate(self, context: ReasoningContext) -> Suggestion:
        user_prompt = (
            "Analizá esta señal y sugerí qué hacer:\n\n"
            + _build_context_block(context)
        )
        try:
            resp = self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=400,
                system=self._system(_SYSTEM_PROMPT),
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
                system=self._system(_REVIEW_SYSTEM_PROMPT),
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

    def plan(self, plan: DeploymentPlan) -> Suggestion:
        """Prioriza el plan de despliegue de cash con Claude (/plan)."""
        try:
            resp = self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=1500,
                system=self._system(_PLAN_SYSTEM_PROMPT),
                messages=[{"role": "user", "content": _plan_user_prompt(plan)}],
            )
        except Exception as exc:  # noqa: BLE001 - normalizamos errores del SDK
            raise ReasoningError(f"Fallo llamando a Anthropic: {exc}") from exc

        if getattr(resp, "stop_reason", None) == "refusal":
            raise ReasoningError("Anthropic rechazó el plan de despliegue.")

        text = "".join(
            block.text
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise ReasoningError("Respuesta vacía de Anthropic.")
        return Suggestion(text=text, source="anthropic")
