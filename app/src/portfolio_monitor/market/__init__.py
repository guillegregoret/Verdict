"""Contexto de mercado: responde "¿es solo una bajada del mercado?".

Calcula el movimiento de los benchmarks (S&P 500, Nasdaq, semis) en la misma
ventana que las señales, y la amplitud (cuántas de mis posiciones están en rojo
en esa ventana). Sin esto el reasoner mira cada ticker aislado y no distingue una
caída sectorial de un deterioro propio del activo.
"""

from __future__ import annotations

from .models import BenchmarkMove, MarketSnapshot
from .service import MarketContextService

__all__ = ["BenchmarkMove", "MarketContextService", "MarketSnapshot"]
