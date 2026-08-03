"""Monitoreo: dead-man's switch (healthchecks.io) + status.json de proveedores (§9)."""

from .health import HealthReport, HealthService, format_health
from .healthcheck import HealthcheckPinger
from .status import MonitoringError, StatusPagePoller

__all__ = [
    "HealthReport",
    "HealthService",
    "HealthcheckPinger",
    "MonitoringError",
    "StatusPagePoller",
    "format_health",
]
