"""Carga de configuración desde entorno (pydantic-settings).

Lee las variables de `.env` (ver `.env.example`). 🔴 READ_ONLY_API debe
permanecer siempre activo — no se expone un flag para desactivarlo desde la app.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la app, poblada desde el entorno."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Entorno ──────────────────────────────────────────────────────────────
    env: str = "dev"
    log_level: str = "INFO"

    # ── Postgres ─────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://portfolio:portfolio@postgres:5432/portfolio",
    )

    # ── Finnhub ──────────────────────────────────────────────────────────────
    finnhub_api_key: str = ""
    finnhub_base_url: str = "https://finnhub.io/api/v1"

    # ── Poller ───────────────────────────────────────────────────────────────
    # Cadencia entre barridos completos y pausa entre requests (rate-limit
    # Finnhub free ~60 req/min). Data delayed ~15 min es aceptable (CLAUDE.md §12).
    poll_interval_seconds: int = 60
    finnhub_request_spacing_seconds: float = 1.1

    # ── Fundamentals (FMP) ───────────────────────────────────────────────────
    # Provider autoritativo de fundamentals (§3). El definitivo está abierto
    # (§13: EDGAR vs FMP vs Finnhub); arrancamos con FMP por su REST de ratios.
    fmp_api_key: str = ""
    fmp_base_url: str = "https://financialmodelingprep.com/api/v3"

    # ── Anthropic (razonamiento) ─────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # ── IB Gateway (🔴 READ-ONLY) ────────────────────────────────────────────
    # El cliente se conecta con readonly=True; el gateway además corre con
    # READ_ONLY_API=yes. Doble red de seguridad: nunca puede operar.
    ib_gateway_host: str = "ib-gateway"
    ib_gateway_port: int = 4002        # 4002 = paper, 4001 = live (ambos read-only)
    ib_gateway_client_id: int = 1

    @property
    def sqlalchemy_url(self) -> str:
        """URL normalizada al driver psycopg 3 que usa SQLAlchemy."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Devuelve la instancia única de configuración (cacheada)."""
    return Settings()
