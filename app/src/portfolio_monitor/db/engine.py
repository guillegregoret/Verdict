"""Engine y conexiones a Postgres/TimescaleDB (SQLAlchemy 2.0 Core).

No define ORM: el schema es autoritativo en `db/migrations/*.sql`. Este módulo
solo provee el engine y un helper de conexión.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from ..config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Devuelve el engine único (pool) hacia Postgres."""
    settings = get_settings()
    return create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,   # descarta conexiones muertas (gateway/restart diario)
    )
