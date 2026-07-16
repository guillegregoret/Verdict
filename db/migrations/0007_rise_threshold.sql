-- ─────────────────────────────────────────────────────────────────────────────
-- 0007 — Umbral de SUBA por ticker (take-profit / consolidar, §5.2)
--
-- `threshold_pct` sigue siendo el umbral de CAÍDA (negativo, ej: -4.5) que usan
-- los veredictos de compra (Crecer/Mantener). `rise_threshold_pct` es el umbral
-- de SUBA (positivo, ej: +8.0) que usan Trim (tomar ganancias) y Consolidar.
-- Así cada dirección se ajusta por separado y por ticker.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE ticker_config
    ADD COLUMN IF NOT EXISTS rise_threshold_pct NUMERIC NOT NULL DEFAULT 4.0;
