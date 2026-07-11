"""Price Poller: ventana móvil de precios → Postgres (§11.2).

Read-only: solo lee precios de Finnhub y los persiste.
"""

from .service import PricePoller

__all__ = ["PricePoller"]
