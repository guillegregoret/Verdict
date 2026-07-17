-- ─────────────────────────────────────────────────────────────────────────────
-- 0012 — Ratings de analistas (§5)
--
-- Snapshots mensuales del consenso de analistas (Finnhub /stock/recommendation).
-- El RatingsMonitor compara el consenso actual vs un baseline y avisa si se
-- deteriora o mejora materialmente.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analyst_ratings (
    ticker      TEXT NOT NULL,
    period      DATE NOT NULL,
    strong_buy  INT,
    buy         INT,
    hold        INT,
    sell        INT,
    strong_sell INT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, period)
);
