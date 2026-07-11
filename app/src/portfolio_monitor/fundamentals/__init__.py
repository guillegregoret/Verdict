"""Fundamentals: fetch de un provider → snapshots en Postgres (§11.5).

Alimenta el chequeo de tesis del trigger (§5.3): "cayó y los fundamentals siguen
sólidos" vs "cayó y el fundamento se deterioró".
"""

from .service import FundamentalsService

__all__ = ["FundamentalsService"]
