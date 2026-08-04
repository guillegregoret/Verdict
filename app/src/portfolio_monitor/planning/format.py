"""Render determinístico del DeploymentPlan a texto (bot + input para Claude)."""

from __future__ import annotations

from .models import AccountPlan, DeploymentCandidate, DeploymentPlan


def _candidate_line(c: DeploymentCandidate) -> str:
    bits = [f"{c.ticker} [{c.verdict}]"]
    bits.append(f"peso {c.weight:.1f}%")
    if c.target_pct is not None:
        bits.append(f"target {c.target_pct:.1f}%")
    if c.unrealized_pct is not None:
        bits.append(f"P&L {c.unrealized_pct:+.1f}%")
    if c.pe is not None:
        bits.append(f"P/E {c.pe:.0f}")
    bits.append(f"cluster {c.cluster or '—'} {c.cluster_weight:.0f}%")
    line = "• " + " · ".join(bits)
    if c.suggested_usd > 0:
        line += (
            f"\n    → desplegar ~${c.suggested_usd:,.0f} en {c.tranches} tramos de "
            f"~${c.tranche_usd:,.0f}, cada uno si cae {c.dip_threshold_pct:.1f}%"
        )
        if c.gap_usd > 0:
            line += f" (gap a target ${c.gap_usd:,.0f})"
    return line


def _account_block(a: AccountPlan) -> str:
    lines = [
        f"🏦 {a.account_name}",
        f"Cash ${a.available_cash:,.0f} · reserva ${a.reserve_usd:,.0f} · "
        f"desplegable ${a.deployable_usd:,.0f} · asignado ${a.allocated_usd:,.0f}",
    ]
    if a.note:
        lines.append(f"ℹ️ {a.note}")
    if not a.candidates:
        lines.append("(sin candidatos de compra en esta cuenta)")
    else:
        lines += [_candidate_line(c) for c in a.candidates]
    return "\n".join(lines)


def format_plan(plan: DeploymentPlan) -> str:
    """Texto del plan determinístico (concreto: $ por nombre, tramos, gatillos)."""
    header = (
        "💸 Plan de despliegue de cash\n"
        f"Cash total ${plan.total_cash:,.0f} · desplegable ${plan.total_deployable:,.0f}"
        f" · asignado ${plan.total_allocated:,.0f}"
    )
    if not plan.has_candidates:
        return header + "\n\n(No hay cash desplegable o candidatos de compra ahora.)"
    blocks = "\n\n".join(_account_block(a) for a in plan.accounts if a.available_cash > 0)
    footer = (
        "\n\nRead-only: es soporte de decisión, no una orden. Vos ejecutás en tu broker."
    )
    return f"{header}\n\n{blocks}{footer}"
