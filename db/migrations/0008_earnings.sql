-- ─────────────────────────────────────────────────────────────────────────────
-- 0008 — Calendario de earnings (§5 informativo)
--
-- Fechas de earnings de los tickers que seguimos (Finnhub /calendar/earnings).
-- Alimenta el aviso de los lunes (earnings de la semana) y una card en los
-- dashboards. `hour`: 'bmo' (before market open) | 'amc' (after market close).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS earnings (
    ticker           TEXT NOT NULL,
    earnings_date    DATE NOT NULL,
    hour             TEXT,               -- 'bmo' | 'amc' | 'dmh' | ''
    eps_estimate     NUMERIC,
    eps_actual       NUMERIC,
    revenue_estimate NUMERIC,
    revenue_actual   NUMERIC,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, earnings_date)
);

CREATE INDEX IF NOT EXISTS earnings_date_idx ON earnings (earnings_date);

-- Próximos earnings (hoy en adelante) con contexto del holding, para el aviso
-- de los lunes y los dashboards.
CREATE OR REPLACE VIEW v_upcoming_earnings AS
SELECT
    e.ticker,
    h.company,
    h.verdict,
    e.earnings_date,
    e.hour,
    e.eps_estimate,
    (e.earnings_date - CURRENT_DATE) AS dias_faltantes
FROM earnings e
LEFT JOIN LATERAL (
    SELECT company, verdict FROM holdings WHERE ticker = e.ticker LIMIT 1
) h ON true
WHERE e.earnings_date >= CURRENT_DATE
ORDER BY e.earnings_date, e.ticker;
