"""Scheduler: orquesta los loops del monolito (§2).

`AlertPipeline` encadena trigger → fundamentals → reasoning → notifier;
`Scheduler` lo corre junto al price poller en un loop periódico.
"""

from .pipeline import AlertPipeline
from .service import Scheduler

__all__ = ["AlertPipeline", "Scheduler"]
