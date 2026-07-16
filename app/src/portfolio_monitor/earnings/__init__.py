"""Earnings: calendario de earnings de los holdings (§5 informativo).

Trae de Finnhub las fechas de earnings de los tickers que seguimos y las
persiste. Alimenta el aviso de los lunes (earnings de la semana) y la card de
los dashboards.
"""

from .service import EarningsService

__all__ = ["EarningsService"]
