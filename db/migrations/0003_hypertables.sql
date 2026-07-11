-- ─────────────────────────────────────────────────────────────────────────────
-- 0003 — Hypertables (TimescaleDB)
-- Convierte las tablas de serie temporal. Debe ir DESPUÉS de crearlas (0002).
-- migrate_data => true por si ya hubiera filas (idempotente en init vacío).
-- ─────────────────────────────────────────────────────────────────────────────

SELECT create_hypertable(
  'prices', 'ts',
  if_not_exists     => TRUE,
  migrate_data      => TRUE,
  chunk_time_interval => INTERVAL '7 days'
);

SELECT create_hypertable(
  'portfolio_snapshots', 'ts',
  if_not_exists     => TRUE,
  migrate_data      => TRUE,
  chunk_time_interval => INTERVAL '30 days'
);
