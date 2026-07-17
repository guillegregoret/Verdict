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
    # Frescura del snapshot: al gatillar un ticker, si el último fundamentals es
    # más viejo que esto se refetchea de FMP (los fundamentals cambian lento —
    # trimestral—, así que 24h evita pegarle a FMP en cada alerta).
    fundamentals_max_age_hours: int = 24

    # ── Deterioro de fundamentals (§5.3) ─────────────────────────────────────
    # Compara el último snapshot contra un baseline ≥ N días más viejo (capta el
    # cambio trimestral). Requiere ese historial acumulado antes de poder disparar.
    fundamentals_refresh_every_ticks: int = 720   # refresca held names cada N ticks (0=off)
    fundamentals_baseline_min_age_days: int = 45  # antigüedad mínima del baseline
    fundamentals_decay_cooldown_days: int = 30    # 1 aviso de deterioro por ticker / N días
    # Umbrales de deterioro (en la escala de cada métrica):
    fund_revenue_growth_drop_pp: float = 15.0  # caída del crecimiento (puntos %)
    fund_margin_drop_pp: float = 5.0           # compresión del margen bruto (puntos %)
    fund_debt_rise: float = 0.5                # salto de deuda/equity (ratio absoluto)

    # ── Earnings (§5 informativo) ────────────────────────────────────────────
    earnings_refresh_every_ticks: int = 1440  # refresca el calendario cada N ticks (0=off)
    earnings_horizon_days: int = 120          # días hacia adelante a traer

    # ── Ratings de analistas (§5) ─────────────────────────────────────────────
    ratings_refresh_every_ticks: int = 1440   # refresca el consenso cada N ticks (0=off)
    ratings_baseline_min_age_days: int = 30    # antigüedad mínima del baseline
    ratings_shift_threshold: float = 0.3       # cambio de score (escala 1-5) que dispara
    ratings_cooldown_days: int = 30            # 1 aviso de cambio por ticker / N días

    # ── Digests semanales (§5) — hora de mercado (New York) ───────────────────
    weekly_digests_enabled: bool = True
    digest_monday_hour_et: int = 8   # lunes: earnings de la semana ~08:00 ET (pre-apertura)
    digest_friday_hour_et: int = 16  # viernes: resumen del portfolio ~16:00 ET (cierre)

    # ── DCA (§5.4) — sizing sugerido en dips, capado al cash de la cuenta ──────
    dca_enabled: bool = True
    dca_default_tranche_usd: float = 100.0  # monto base si el ticker no tiene plan propio
    dca_max_multiplier: float = 2.0         # tope del factor por profundidad del dip
    dca_dip_slope: float = 0.1              # +multiplicador por cada 1% de caída

    # ── Anthropic (razonamiento) ─────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # ── Telegram (notificaciones) ────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_base_url: str = "https://api.telegram.org"
    # Bot interactivo /status (§5). 🔴 FAIL-CLOSED: sin ids acá, el bot no
    # responde datos a nadie (solo /whoami). CSV de tu user id personal.
    telegram_bot_enabled: bool = True
    telegram_allowed_user_ids: str = ""

    # ── Monitoreo (§9) ───────────────────────────────────────────────────────
    # Dead-man's switch: la app pinga esta URL cada tick. Si deja de pingar → aviso.
    healthchecks_ping_url: str = ""

    # ── IB Gateway (🔴 READ-ONLY) ────────────────────────────────────────────
    # El cliente se conecta con readonly=True; el gateway además corre con
    # READ_ONLY_API=yes. Doble red de seguridad: nunca puede operar.
    # La imagen gnzsnz/ib-gateway bindea 4001/4002 a localhost DEL contenedor y
    # publica vía socat 4003 (live) / 4004 (paper) hacia otros contenedores.
    ib_gateway_host: str = "ib-gateway"
    ib_gateway_port: int = 4004        # 4004 = paper, 4003 = live (ambos read-only)
    ib_gateway_client_id: int = 1
    # Cadencia del sync de holdings: 1 cada N ticks (las posiciones cambian lento;
    # no reconectar al gateway en cada barrido). 0 = deshabilitado.
    holdings_sync_every_ticks: int = 60

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
