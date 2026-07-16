-- ─────────────────────────────────────────────────────────────────────────────
-- 0009 — Log de digests semanales (dedupe)
--
-- Evita mandar dos veces el mismo aviso semanal: el loop chequea cada tick si
-- ya se envió hoy (fecha en hora de New York). PK (kind, sent_on) = idempotente.
--   kind: 'monday_earnings' | 'friday_summary'
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS digest_log (
    kind    TEXT NOT NULL,
    sent_on DATE NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, sent_on)
);
