"""Tests del PortfolioReportRenderer (HTML del email)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from portfolio_monitor.reasoning import (
    ClusterExposure,
    PortfolioReviewContext,
    ReviewPosition,
)
from portfolio_monitor.report import PortfolioReportRenderer

NOW = datetime(2026, 7, 22, 14, 30, tzinfo=UTC)


def _context() -> PortfolioReviewContext:
    positions = (
        ReviewPosition("NVDA", 18.0, "Mantener", 4140.0, "P/E 31.0, crec +70%",
                       earnings=date(2026, 8, 4), unrealized_pct=12.0, target_pct=12.0),
        ReviewPosition("GOOG", 6.0, "Crecer", 1380.0, "P/E 26.0, crec +17%",
                       unrealized_pct=-30.8),
        ReviewPosition("MU", 4.0, "Trim - tomar ganancias", 920.0, "P/E 21.0"),
    )
    return PortfolioReviewContext(
        positions_block="…",
        cash_block="• IA: $69 USD",
        total_value=23000.0,
        total_cash=564.0,
        position_count=3,
        note="posiciones pesadas → NVDA 18%",
        positions=positions,
        audit_flags=("MU [Trim - tomar ganancias]: veredicto de recorte pero P/E 17",),
        clusters=(
            ClusterExposure("Compute/GPU", 24.0, 2, -3.1),
            ClusterExposure("Salud", 6.0, 1, 4.2),
        ),
    )


def test_subject_has_product_and_value() -> None:
    subj = PortfolioReportRenderer().subject(_context(), now=NOW)
    assert "Verdict" in subj
    assert "$23,000" in subj


def test_html_has_brand_tiles_and_footer() -> None:
    html = PortfolioReportRenderer().render_html(_context(), "# Review\ntext", now=NOW)
    assert "GREGORET INDUSTRIES" in html
    assert "Verdict" in html
    assert "$23,000" in html          # tile market value
    assert "$564" in html             # tile cash
    assert "READ-ONLY" in html        # disclaimer
    assert "posiciones pesadas" in html  # concentration note


def test_html_renders_allocation_bars_and_chips() -> None:
    html = PortfolioReportRenderer().render_html(_context(), "x", now=NOW)
    assert "NVDA" in html and "GOOG" in html
    assert "width:100.0%" in html     # NVDA es el mayor peso → barra full
    assert "18.0%" in html            # etiqueta de peso
    assert "Trim - tomar ganancias" in html  # chip de veredicto textual
    assert "Aug 04" in html           # earnings anotado


def test_html_shows_unrealized_pnl_per_position_and_total() -> None:
    html = PortfolioReportRenderer().render_html(_context(), "x", now=NOW)
    assert "+12.0%" in html          # NVDA en verde
    assert "-30.8%" in html          # GOOG en rojo
    assert "#ff7a7a" in html         # color de pérdida
    assert "UNREALIZED P&amp;L" in html or "UNREALIZED P&L" in html
    assert "3 positions" in html     # el conteo se movió al título de Allocation


def test_position_without_cost_renders_empty_pnl_cell() -> None:
    html = PortfolioReportRenderer().render_html(_context(), "x", now=NOW)
    assert "<td class='pnl'></td>" in html  # MU no tiene costo cargado


def test_html_shows_cluster_exposure() -> None:
    html = PortfolioReportRenderer().render_html(_context(), "x", now=NOW)
    assert "Exposure by Cluster" in html
    assert "Compute/GPU" in html
    assert "24.0%" in html


def test_text_version_includes_clusters() -> None:
    txt = PortfolioReportRenderer().render_text(_context(), "body", now=NOW)
    assert "Exposure by cluster" in txt
    assert "Compute/GPU: 24.0%" in txt


def test_html_shows_target_drift_when_material() -> None:
    # NVDA 18.0% con target 12.0% → +6.0pp sobre target → se anota.
    html = PortfolioReportRenderer().render_html(_context(), "x", now=NOW)
    assert "target 12.0%" in html
    assert "+6.0pp" in html


def test_html_shows_verdict_audit_section() -> None:
    html = PortfolioReportRenderer().render_html(_context(), "x", now=NOW)
    assert "Verdict Audit" in html
    assert "veredicto de recorte pero P/E 17" in html


def test_text_version_includes_audit() -> None:
    txt = PortfolioReportRenderer().render_text(_context(), "body", now=NOW)
    assert "Verdict audit" in txt
    assert "MU [Trim" in txt


def test_no_audit_section_without_flags() -> None:
    ctx = _context()
    ctx = PortfolioReviewContext(
        positions_block=ctx.positions_block,
        cash_block=ctx.cash_block,
        total_value=ctx.total_value,
        total_cash=ctx.total_cash,
        position_count=ctx.position_count,
        positions=ctx.positions,
    )
    html = PortfolioReportRenderer().render_html(ctx, "x", now=NOW)
    assert "Verdict Audit" not in html


def test_markdown_analysis_is_converted() -> None:
    md = "## Thesis\n- **NVDA** holds\n- GOOG cheap"
    html = PortfolioReportRenderer().render_html(_context(), md, now=NOW)
    assert "<h2" in html
    assert "<strong>NVDA</strong>" in html
    assert "<li>" in html


def test_text_version_is_plain() -> None:
    txt = PortfolioReportRenderer().render_text(_context(), "analysis body", now=NOW)
    assert "Gregoret Industries" in txt
    assert "analysis body" in txt
    assert "<" not in txt  # sin HTML
