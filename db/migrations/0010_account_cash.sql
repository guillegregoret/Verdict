-- ─────────────────────────────────────────────────────────────────────────────
-- 0010 — Cash disponible por cuenta (para el DCA, §5.4)
--
-- Snapshots del cash de cada cuenta IBKR (AvailableFunds/TotalCashValue), que el
-- motor de DCA usa para no sugerir más de lo que hay para desplegar.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS account_cash (
    account_id      INT NOT NULL REFERENCES accounts(id),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_cash      NUMERIC,
    available_funds NUMERIC,
    currency        TEXT NOT NULL DEFAULT 'USD',
    PRIMARY KEY (account_id, ts)
);

-- Último cash conocido por cuenta (para el DCA y los dashboards).
CREATE OR REPLACE VIEW v_account_cash_latest AS
SELECT DISTINCT ON (a.id)
    a.ibkr_id,
    a.name,
    c.total_cash,
    c.available_funds,
    c.currency,
    c.ts
FROM accounts a
JOIN account_cash c ON c.account_id = a.id
ORDER BY a.id, c.ts DESC;
