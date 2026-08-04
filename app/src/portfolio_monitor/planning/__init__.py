"""Planificador de despliegue de cash (/plan).

Código arma el scaffold cuantitativo (cash por cuenta, gap-a-target, tramos,
gatillos); el reasoner prioriza y narra. Read-only: prepara la decisión.
"""

from __future__ import annotations

from .format import format_plan
from .models import AccountPlan, DeploymentCandidate, DeploymentPlan
from .service import CashPlanService, PlanService

__all__ = [
    "AccountPlan",
    "CashPlanService",
    "DeploymentCandidate",
    "DeploymentPlan",
    "PlanService",
    "format_plan",
]
