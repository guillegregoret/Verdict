"""Sync de holdings desde IBKR → Postgres (§11.3).

Read-only: lee posiciones del gateway y las persiste, preservando la config de
veredictos del usuario.
"""

from .service import HoldingsSyncService

__all__ = ["HoldingsSyncService"]
