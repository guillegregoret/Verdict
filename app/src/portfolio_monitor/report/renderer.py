"""PortfolioReportRenderer: HTML del reporte de reevaluación (email).

Toma el `PortfolioReviewContext` (posiciones, pesos, cash) + el análisis en
Markdown de Claude y arma un email HTML branded (Gregoret Industries · Verdict),
dark futurista minimalista, en inglés. Gráficos como barras CSS (bulletproof en
todos los clientes de mail; no dependen de imágenes ni JS).
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

import markdown as md

from ..reasoning import PortfolioReviewContext, ReviewPosition

_BRAND = "Gregoret Industries"
_PRODUCT = "Verdict"
_TAGLINE = "Portfolio Intelligence"

# Paleta dark futurista.
_BG = "#0a0e17"
_PANEL = "#111725"
_PANEL_2 = "#0d1320"
_BORDER = "#1e2a3f"
_TEXT = "#e6edf7"
_MUTED = "#8b9bb4"
_ACCENT = "#3dd7d0"       # cyan
_ACCENT_2 = "#5b8cff"     # azul eléctrico
_GAIN = "#3ddc97"         # P&L no realizado en verde
_LOSS = "#ff7a7a"         # P&L no realizado en rojo

# Color del chip por familia de veredicto (buy / hold / cap / reduce).
_VERDICT_COLORS = {
    "crecer": ("#0e3b2e", "#3ddc97"),
    "mantener - no sumar": ("#3b2f0e", "#e7c14b"),
    "trim": ("#3b1a1a", "#ff7a7a"),
    "consolidar": ("#2a1a3b", "#c08bff"),
    "objetivo": ("#1c2430", "#8b9bb4"),
    "mantener": ("#0e2a3b", "#5bd0ff"),
}


def _total_unrealized_pct(positions: tuple[ReviewPosition, ...]) -> float | None:
    """P&L no realizado del portfolio (%), ponderado por costo.

    Reconstruye el costo de cada posición desde su valor de mercado y su P&L
    (`mv / (1 + pnl)`), y suma. None si ninguna posición tiene dato de costo.
    """
    cost = value = 0.0
    for p in positions:
        if p.unrealized_pct is None:
            continue
        factor = 1 + p.unrealized_pct / 100
        if factor <= 0:
            continue
        cost += p.market_value / factor
        value += p.market_value
    if cost == 0:
        return None
    return (value - cost) / cost * 100.0


def _target_html(p: ReviewPosition) -> str:
    """Anotación de drift contra el target, solo si es material (evita ruido)."""
    drift = p.weight_drift
    if drift is None or abs(drift) < 2.0:
        return ""
    color = _LOSS if drift > 0 else _ACCENT_2  # sobre target = recortar; bajo = sumar
    arrow = "↑" if drift > 0 else "↓"
    return (
        f"<span class='drift' style='color:{color};'> · {arrow} target "
        f"{p.target_pct:.1f}% ({drift:+.1f}pp)</span>"
    )


def _verdict_style(verdict: str) -> tuple[str, str]:
    key = verdict.strip().lower()
    for token, colors in _VERDICT_COLORS.items():
        if token in key:
            return colors
    return ("#1c2430", _MUTED)


class PortfolioReportRenderer:
    """Renderiza el reporte de reevaluación a HTML (y texto plano) para email."""

    def __init__(self, brand: str = _BRAND) -> None:
        self._brand = brand

    # ── API pública ──────────────────────────────────────────────────────────
    def subject(self, context: PortfolioReviewContext, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        return (
            f"{_PRODUCT} — Portfolio Review · ${context.total_value:,.0f} · "
            f"{now:%b %d, %Y}"
        )

    def render_html(
        self,
        context: PortfolioReviewContext,
        analysis_markdown: str,
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        analysis_html = md.markdown(
            analysis_markdown, extensions=["extra", "sane_lists", "nl2br"]
        )
        return "".join([
            self._doc_open(),
            self._header(now),
            self._tiles(context),
            self._audit(context.audit_flags),
            self._allocation(context.positions),
            self._analysis(analysis_html),
            self._footer(now),
            self._doc_close(),
        ])

    def render_text(
        self,
        context: PortfolioReviewContext,
        analysis_markdown: str,
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        total_pnl = _total_unrealized_pct(context.positions)
        pnl = f" · P&L {total_pnl:+.1f}%" if total_pnl is not None else ""
        audit = ""
        if context.audit_flags:
            audit = "\nVerdict audit:\n" + "\n".join(
                f"  ⚠ {flag}" for flag in context.audit_flags
            ) + "\n"
        return (
            f"{self._brand} · {_PRODUCT} — Portfolio Review ({now:%b %d, %Y})\n"
            f"Market value ${context.total_value:,.0f} · Cash "
            f"${context.total_cash:,.0f} · {context.position_count} positions{pnl}\n"
            f"{audit}\n"
            f"{analysis_markdown}\n"
        )

    # ── Secciones ────────────────────────────────────────────────────────────
    def _doc_open(self) -> str:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='color-scheme' content='dark'>"
            f"<style>{_CSS}</style></head>"
            f"<body style='margin:0;background:{_BG};'>"
            "<div class='wrap'><div class='card'>"
        )

    def _doc_close(self) -> str:
        return "</div></div></body></html>"

    def _header(self, now: datetime) -> str:
        return (
            "<div class='hdr'>"
            "<div class='brand'>"
            "<span class='mono'>◆</span>"
            f"<span class='brandname'>{html.escape(self._brand.upper())}</span>"
            "</div>"
            f"<div class='product'>{_PRODUCT} "
            f"<span class='tag'>· {_TAGLINE}</span></div>"
            f"<div class='date'>{now:%A, %B %d, %Y}</div>"
            "</div>"
        )

    def _tiles(self, ctx: PortfolioReviewContext) -> str:
        tiles: list[tuple[str, str, str]] = [
            ("MARKET VALUE", f"${ctx.total_value:,.0f}", _TEXT),
            ("AVAILABLE CASH", f"${ctx.total_cash:,.0f}", _TEXT),
        ]
        total_pnl = _total_unrealized_pct(ctx.positions)
        if total_pnl is None:
            tiles.append(("POSITIONS", str(ctx.position_count), _TEXT))
        else:
            tiles.append((
                "UNREALIZED P&L",
                f"{total_pnl:+.1f}%",
                _GAIN if total_pnl >= 0 else _LOSS,
            ))
        cells = "".join(
            f"<td class='tile'><div class='tlabel'>{label}</div>"
            f"<div class='tvalue' style='color:{color};'>{value}</div></td>"
            for label, value, color in tiles
        )
        note = (
            f"<div class='concern'>⚠ {html.escape(ctx.note)}</div>"
            if ctx.note else ""
        )
        return (
            "<table class='tiles' width='100%' cellpadding='0' cellspacing='0'>"
            f"<tr>{cells}</tr></table>{note}"
        )

    def _audit(self, flags: tuple[str, ...]) -> str:
        """Sección de veredictos que contradicen los fundamentals (si hay)."""
        if not flags:
            return ""
        items = "".join(f"<li>{html.escape(flag)}</li>" for flag in flags)
        return (
            "<div class='sec'><div class='sectitle'>Verdict Audit</div>"
            f"<ul class='audit'>{items}</ul></div>"
        )

    def _allocation(self, positions: tuple[ReviewPosition, ...]) -> str:
        if not positions:
            return ""
        top = max((p.weight for p in positions), default=0.0) or 1.0
        rows = []
        for p in positions:
            bg, fg = _verdict_style(p.verdict)
            fill = max(2.0, p.weight / top * 100)
            earn = (
                f"<span class='earn'>📅 {p.earnings:%b %d}</span>" if p.earnings else ""
            )
            # P&L contra el costo promedio: es lo que distingue "subió el papel"
            # de "gané plata". Sin dato de costo, la celda queda vacía.
            if p.unrealized_pct is None:
                pnl = "<td class='pnl'></td>"
            else:
                color = _GAIN if p.unrealized_pct >= 0 else _LOSS
                pnl = (
                    f"<td class='pnl' style='color:{color};'>"
                    f"{p.unrealized_pct:+.1f}%</td>"
                )
            rows.append(
                "<tr class='arow'>"
                f"<td class='tkr'>{html.escape(p.ticker)}</td>"
                "<td class='barc'>"
                f"<div class='bar'><div class='fill' style='width:{fill:.1f}%;"
                f"background:linear-gradient(90deg,{_ACCENT_2},{_ACCENT});'></div></div>"
                f"<div class='fund'>{html.escape(p.fundamentals_text)}{earn}"
                f"{_target_html(p)}</div>"
                "</td>"
                f"<td class='wt'>{p.weight:.1f}%</td>"
                f"{pnl}"
                f"<td class='vd'><span class='chip' style='background:{bg};"
                f"color:{fg};'>{html.escape(p.verdict)}</span></td>"
                "</tr>"
            )
        return (
            f"<div class='sec'><div class='sectitle'>Allocation · "
            f"{len(positions)} positions</div>"
            "<table class='alloc' width='100%' cellpadding='0' cellspacing='0'>"
            + "".join(rows)
            + "</table></div>"
        )

    def _analysis(self, analysis_html: str) -> str:
        return (
            "<div class='sec'><div class='sectitle'>Analyst Review</div>"
            f"<div class='prose'>{analysis_html}</div></div>"
        )

    def _footer(self, now: datetime) -> str:
        return (
            "<div class='ftr'>"
            f"<span class='mono'>◆</span> {html.escape(self._brand)} · {_PRODUCT} — "
            "READ-ONLY. This is not investment advice; you decide and execute "
            "manually in your broker."
            f"<div class='fdate'>Generated {now:%Y-%m-%d %H:%M UTC}</div>"
            "</div>"
        )


_CSS = f"""
.wrap{{padding:24px 12px;background:{_BG};font-family:-apple-system,BlinkMacSystemFont,
'Segoe UI',Roboto,Helvetica,Arial,sans-serif;}}
.card{{max-width:640px;margin:0 auto;background:{_PANEL};border:1px solid {_BORDER};
border-radius:16px;overflow:hidden;}}
.hdr{{padding:26px 28px 20px;border-bottom:1px solid {_BORDER};
background:linear-gradient(180deg,{_PANEL_2},{_PANEL});}}
.brand{{display:flex;align-items:center;gap:9px;}}
.mono{{color:{_ACCENT};font-size:13px;}}
.brandname{{color:{_TEXT};font-size:13px;letter-spacing:4px;font-weight:600;}}
.product{{color:{_TEXT};font-size:26px;font-weight:700;margin-top:10px;
letter-spacing:-.5px;}}
.tag{{color:{_ACCENT};font-size:14px;font-weight:500;letter-spacing:.5px;}}
.date{{color:{_MUTED};font-size:12px;margin-top:6px;letter-spacing:.3px;}}
.tiles{{padding:0;margin:0;border-collapse:separate;}}
.tile{{padding:20px 12px;text-align:center;border-right:1px solid {_BORDER};
border-bottom:1px solid {_BORDER};}}
.tile:last-child{{border-right:0;}}
.tlabel{{color:{_MUTED};font-size:10px;letter-spacing:1.5px;}}
.tvalue{{color:{_TEXT};font-size:22px;font-weight:700;margin-top:6px;
font-variant-numeric:tabular-nums;}}
.concern{{color:#e7c14b;background:#241d08;padding:11px 16px;font-size:13px;
border-bottom:1px solid {_BORDER};}}
.audit{{margin:0;padding-left:20px;color:{_LOSS};font-size:13px;line-height:1.55;}}
.audit li{{margin:6px 0;}}
.sec{{padding:22px 28px;border-bottom:1px solid {_BORDER};}}
.sectitle{{color:{_ACCENT};font-size:11px;letter-spacing:2px;text-transform:uppercase;
margin-bottom:16px;}}
.alloc{{border-collapse:collapse;}}
.arow td{{padding:7px 0;vertical-align:middle;}}
.tkr{{color:{_TEXT};font-weight:700;font-size:13px;width:56px;white-space:nowrap;}}
.barc{{padding-left:4px!important;padding-right:10px!important;}}
.bar{{background:{_PANEL_2};border-radius:6px;height:9px;overflow:hidden;
border:1px solid {_BORDER};}}
.fill{{height:9px;border-radius:6px;}}
.fund{{color:{_MUTED};font-size:11px;margin-top:4px;}}
.earn{{color:{_ACCENT_2};margin-left:8px;}}
.drift{{font-weight:600;}}
.wt{{color:{_TEXT};font-size:13px;font-weight:600;text-align:right;width:48px;
font-variant-numeric:tabular-nums;}}
.pnl{{font-size:12px;font-weight:600;text-align:right;width:60px;
padding-left:10px!important;font-variant-numeric:tabular-nums;}}
.vd{{text-align:right;width:150px;padding-left:10px;}}
.chip{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;
font-weight:600;letter-spacing:.3px;white-space:nowrap;}}
.prose{{color:{_TEXT};font-size:14px;line-height:1.6;}}
.prose h1,.prose h2{{color:{_TEXT};font-size:16px;margin:20px 0 8px;
border-bottom:1px solid {_BORDER};padding-bottom:6px;}}
.prose h3{{color:{_ACCENT};font-size:13px;letter-spacing:.5px;margin:16px 0 6px;
text-transform:uppercase;}}
.prose strong{{color:#fff;}}
.prose ul{{margin:6px 0;padding-left:20px;}}
.prose li{{margin:5px 0;}}
.prose hr{{border:0;border-top:1px solid {_BORDER};margin:16px 0;}}
.prose p{{margin:8px 0;}}
.prose em{{color:{_MUTED};font-style:italic;}}
.ftr{{padding:20px 28px;color:{_MUTED};font-size:11px;line-height:1.6;}}
.fdate{{margin-top:6px;color:{_MUTED};opacity:.7;}}
"""
