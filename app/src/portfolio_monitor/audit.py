"""Auditoría de veredictos: detecta cuando el label configurado contradice los
fundamentals (§4 son config estática y nadie los revisa).

El caso que motiva esto: un ticker con veredicto `Trim - tomar ganancias` cuyo
P/E es bajo y su crecimiento altísimo — nada en los fundamentals respalda
"recortar". El veredicto es config vieja que se pudre en silencio; el sistema no
tiene forma de avisar que su propia etiqueta ya no cuadra. Esta función marca esas
contradicciones para que el reasoner (y el reporte) las expongan.

Read-only y conservador: solo marca contradicciones claras (evita el ruido de
flaggear medio portfolio). Cada flag trae SU motivo, legible.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db.repositories import FundamentalsRow

# Umbrales (deliberadamente conservadores para no flaggear de más):
_PE_CHEAP = 25.0          # P/E por debajo del cual "está caro" no aplica
_GROWTH_STRONG = 0.25     # crecimiento de ingresos "fuerte" (fracción, 25%)
_GROWTH_SHRINKING = -0.05  # ingresos cayendo de verdad (fracción, -5%)

# Familias de veredicto (se comparan en minúscula, por substring).
_REDUCE_TOKENS = ("trim", "consolidar")  # recortar / rotar
_BUY_TOKENS = ("crecer", "mantener")     # sumar en el dip
_NO_ADD_TOKEN = "no sumar"               # 'Mantener - no sumar' NO es de compra


@dataclass(frozen=True)
class VerdictAudit:
    """Una contradicción detectada entre el veredicto y los fundamentals."""

    ticker: str
    verdict: str
    issue: str  # motivo legible de la contradicción


def _is_reduce(verdict: str) -> bool:
    return any(tok in verdict for tok in _REDUCE_TOKENS)


def _is_buy(verdict: str) -> bool:
    return _NO_ADD_TOKEN not in verdict and any(tok in verdict for tok in _BUY_TOKENS)


def audit_verdict(
    ticker: str, verdict: str, fundamentals: FundamentalsRow | None
) -> VerdictAudit | None:
    """Marca si el veredicto contradice los fundamentals (o None si cuadra).

    Dos contradicciones claras:
    - RECORTAR (Trim/Consolidar) un activo barato y creciendo fuerte: el
      argumento de "tomar ganancias / consolidar por valuación" no lo respaldan
      los números (ej: MU con P/E 17 y +167% de ingresos).
    - SUMAR (Crecer/Mantener) un activo cuyos ingresos están cayendo: la tesis de
      compra en el dip quedó vieja, conviene revisarla antes de agregar.
    """
    if fundamentals is None:
        return None
    v = verdict.strip().lower()
    pe = fundamentals.pe
    growth = fundamentals.revenue_growth

    if _is_reduce(v) and pe is not None and growth is not None:
        if pe <= _PE_CHEAP and growth >= _GROWTH_STRONG:
            return VerdictAudit(
                ticker=ticker,
                verdict=verdict,
                issue=(
                    f"veredicto de recorte pero P/E {pe:.0f} (barato) y crecimiento "
                    f"de ingresos {growth * 100:+.0f}% (fuerte): los fundamentals no "
                    f"respaldan tomar ganancias por valuación"
                ),
            )

    if _is_buy(v) and growth is not None and growth <= _GROWTH_SHRINKING:
        return VerdictAudit(
            ticker=ticker,
            verdict=verdict,
            issue=(
                f"veredicto de compra pero ingresos cayendo {growth * 100:+.0f}%: "
                f"revisá la tesis antes de sumar en la baja"
            ),
        )

    return None
