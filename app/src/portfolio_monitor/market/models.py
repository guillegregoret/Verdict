"""Modelos del contexto de mercado (amplitud + benchmarks)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkMove:
    """Movimiento de un benchmark en la ventana (S&P 500 -1.8%, …)."""

    label: str
    pct_change: float


@dataclass(frozen=True)
class MarketSnapshot:
    """Foto del mercado en la ventana: benchmarks + amplitud de mis posiciones.

    - `benchmarks`: movimiento de cada índice/ETF de referencia.
    - `breadth_down`/`breadth_total`: cuántas de mis posiciones están en rojo en la
      misma ventana (la señal de "es el mercado" vs "es este activo").
    """

    benchmarks: tuple[BenchmarkMove, ...]
    breadth_down: int
    breadth_total: int
    window_minutes: int

    @property
    def has_data(self) -> bool:
        """True si hay al menos un benchmark o amplitud para reportar."""
        return bool(self.benchmarks) or self.breadth_total > 0

    def summary(self) -> str:
        """Línea legible para el prompt/reporte (vacía si no hay datos)."""
        if not self.has_data:
            return ""
        parts = [f"{b.label} {b.pct_change:+.1f}%" for b in self.benchmarks]
        market = ", ".join(parts) if parts else "sin benchmarks"
        if self.breadth_total:
            market += (
                f" · {self.breadth_down}/{self.breadth_total} de mis posiciones "
                f"en rojo en la ventana"
            )
        return market
