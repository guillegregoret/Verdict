"""Ratings de analistas (§5): avisa cuando el consenso se mueve materialmente.

Trae de Finnhub el consenso (strongBuy/buy/hold/sell/strongSell), lo persiste y
compara el score actual vs un baseline. Si mejora o se deteriora más que el
umbral, emite una señal para el pipeline.
"""

from .monitor import RatingEvent, RatingsMonitor
from .service import RatingsService

__all__ = ["RatingEvent", "RatingsMonitor", "RatingsService"]
