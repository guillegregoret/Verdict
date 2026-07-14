"""Fundamentals: fetch de un provider → snapshots en Postgres (§11.5).

Alimenta el chequeo de tesis del trigger (§5.3): "cayó y los fundamentals siguen
sólidos" vs "cayó y el fundamento se deterioró". El FundamentalsMonitor además
avisa cuando la tesis se deteriora sola (sin importar el precio).
"""

from .monitor import FundamentalsEvent, FundamentalsMonitor
from .service import FundamentalsRefreshService, FundamentalsService

__all__ = [
    "FundamentalsEvent",
    "FundamentalsMonitor",
    "FundamentalsRefreshService",
    "FundamentalsService",
]
