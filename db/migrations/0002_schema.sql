-- ─────────────────────────────────────────────────────────────────────────────
-- 0002 — Schema base (ver CLAUDE.md §6)
-- Sistema READ-ONLY: estas tablas solo registran observaciones y alertas.
-- Ninguna representa órdenes ni ejecución.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cuentas ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
  id          SERIAL PRIMARY KEY,
  ibkr_id     TEXT UNIQUE,          -- U22106929, etc. NULL para eToro (legacy)
  name        TEXT NOT NULL,
  role        TEXT NOT NULL         -- 'ia' | 'salud_defensa' | 'core_ucits' | 'legacy'
);

-- ── Posiciones (estado actual; se sincroniza desde IBKR read-only) ───────────
CREATE TABLE IF NOT EXISTS holdings (
  id          SERIAL PRIMARY KEY,
  account_id  INT REFERENCES accounts(id),
  ticker      TEXT NOT NULL,
  company     TEXT,
  cluster     TEXT,                 -- Compute/GPU, Networking, Salud, Defensa, ...
  shares      NUMERIC,
  avg_cost    NUMERIC,
  verdict     TEXT NOT NULL,        -- ver CLAUDE.md §4 (mapea al Verdict Gate)
  target_pct  NUMERIC,              -- % objetivo dentro del sleeve (opcional)
  thesis      TEXT,
  updated_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE(account_id, ticker)
);

-- ── Config de triggers por ticker ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticker_config (
  ticker          TEXT PRIMARY KEY,
  threshold_pct   NUMERIC DEFAULT -2.7,
  window_minutes  INT DEFAULT 390,  -- ~1 rueda US
  enabled         BOOLEAN DEFAULT true
);

-- ── Serie de precios (se convierte en hypertable en 0003) ────────────────────
CREATE TABLE IF NOT EXISTS prices (
  ticker  TEXT NOT NULL,
  ts      TIMESTAMPTZ NOT NULL,
  price   NUMERIC NOT NULL,
  source  TEXT,                     -- 'finnhub' | 'yfinance' | 'ibkr'
  PRIMARY KEY (ticker, ts)
);

-- ── Snapshots de portfolio (histórico de totales por cuenta) ─────────────────
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  account_id     INT REFERENCES accounts(id),
  ts             TIMESTAMPTZ NOT NULL,
  market_value   NUMERIC,
  unrealized_pl  NUMERIC,
  cash           NUMERIC,
  PRIMARY KEY (account_id, ts)
);

-- ── Fundamentals (snapshots para chequeo de tesis) ───────────────────────────
CREATE TABLE IF NOT EXISTS fundamentals (
  ticker          TEXT NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,
  pe              NUMERIC,
  revenue_growth  NUMERIC,
  gross_margin    NUMERIC,
  debt_to_equity  NUMERIC,
  raw             JSONB,            -- payload completo por si acaso
  source          TEXT,
  PRIMARY KEY (ticker, ts)
);

-- ── Alertas emitidas (auditoría + cooldown) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
  id               SERIAL PRIMARY KEY,
  ticker           TEXT NOT NULL,
  ts               TIMESTAMPTZ DEFAULT now(),
  trigger_type     TEXT,            -- 'drop_pct'
  pct_change       NUMERIC,
  window_minutes   INT,
  verdict          TEXT,
  suggestion       TEXT,            -- texto devuelto por Anthropic
  bucket_remaining NUMERIC,
  sent_via         TEXT DEFAULT 'telegram',
  status           TEXT DEFAULT 'sent'
);

-- ── Salud de fuentes de datos (para el monitoreo) ────────────────────────────
CREATE TABLE IF NOT EXISTS data_source_health (
  source     TEXT NOT NULL,
  ts         TIMESTAMPTZ DEFAULT now(),
  status     TEXT,                  -- 'up' | 'down' | 'degraded'
  latency_ms INT
);

-- ── Índices de apoyo ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_alerts_ticker_ts       ON alerts (ticker, ts DESC);
CREATE INDEX IF NOT EXISTS idx_holdings_account       ON holdings (account_id);
CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker_ts ON fundamentals (ticker, ts DESC);
CREATE INDEX IF NOT EXISTS idx_dsh_source_ts          ON data_source_health (source, ts DESC);
