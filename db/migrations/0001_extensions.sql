-- ─────────────────────────────────────────────────────────────────────────────
-- 0001 — Extensiones
-- Se ejecuta primero (orden alfabético) en un volumen de datos vacío.
-- ─────────────────────────────────────────────────────────────────────────────

-- TimescaleDB: hypertables para series temporales (precios, snapshots).
CREATE EXTENSION IF NOT EXISTS timescaledb;
