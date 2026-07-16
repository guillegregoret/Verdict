"""Digests semanales (§5): aviso de earnings (lunes) + resumen del portfolio (viernes).

Se disparan por hora de mercado (New York) desde el loop, con dedupe por día.
"""

from .service import EarningsDigest, PortfolioDigest, WeeklyDigestRunner

__all__ = ["EarningsDigest", "PortfolioDigest", "WeeklyDigestRunner"]
