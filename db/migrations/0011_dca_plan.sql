-- ─────────────────────────────────────────────────────────────────────────────
-- 0011 — Plan de DCA por ticker (§5.4)
--
-- Overrides opcionales por ticker del tranche base y el tope del multiplicador.
-- Si un ticker no tiene fila acá, el sizer usa los defaults de Settings
-- (DCA_DEFAULT_TRANCHE_USD / DCA_MAX_MULTIPLIER). Es config del usuario.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dca_plan (
    ticker         TEXT PRIMARY KEY,
    tranche_usd    NUMERIC NOT NULL,          -- monto base a comprar en un dip
    max_multiplier NUMERIC NOT NULL DEFAULT 2.0,  -- tope del factor por profundidad
    enabled        BOOLEAN NOT NULL DEFAULT true
);
