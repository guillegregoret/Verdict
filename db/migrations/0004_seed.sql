-- ─────────────────────────────────────────────────────────────────────────────
-- 0004 — Seed inicial (ver CLAUDE.md §4)
-- Cuentas, veredictos por ticker y config de triggers.
-- Los veredictos son CONFIG: viven en la DB y se editan sin tocar código.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cuentas ──────────────────────────────────────────────────────────────────
INSERT INTO accounts (ibkr_id, name, role) VALUES
  ('U22106929', 'Satélite IA (Picks & Shovels)', 'ia'),
  ('U26716079', 'Satélite Salud/Defensa',        'salud_defensa'),
  (NULL,        'Core UCITS (VWCE)',             'core_ucits'),
  (NULL,        'eToro (legacy)',                'legacy')
ON CONFLICT (ibkr_id) DO NOTHING;

-- ── Holdings: veredicto por ticker ───────────────────────────────────────────
-- Verdict Gate (§4): Crecer/Mantener → candidato de compra en dip.
--                    Mantener - no sumar / Trim / Consolidar → NO sugerir compra.
--                    Objetivo (sin comprar) → fase 2, aún no activo.

-- Cuenta IA (U22106929)
INSERT INTO holdings (account_id, ticker, company, cluster, verdict)
SELECT a.id, v.ticker, v.company, v.cluster, v.verdict
FROM accounts a
JOIN (VALUES
  ('NVDA', 'NVIDIA',            'Compute/GPU', 'Mantener'),
  ('AVGO', 'Broadcom',          'Networking',  'Mantener'),
  ('ASML', 'ASML',              'Semicap',     'Mantener'),
  ('ANET', 'Arista Networks',   'Networking',  'Mantener'),
  ('TSM',  'TSMC',              'Foundry',     'Mantener'),
  ('MSFT', 'Microsoft',         'Hyperscaler', 'Mantener'),
  ('VRT',  'Vertiv',            'Power/Cooling','Mantener'),
  ('ETN',  'Eaton',             'Power',       'Mantener'),
  ('ARM',  'Arm Holdings',      'Compute/IP',  'Mantener'),
  ('GEV',  'GE Vernova',        'Power',       'Mantener'),
  ('GOOG', 'Alphabet',          'Hyperscaler', 'Crecer'),
  ('MU',   'Micron',            'Memory',      'Trim - tomar ganancias'),
  ('CEG',  'Constellation',     'Power',       'Mantener - no sumar'),
  ('AMD',  'AMD',               'Compute/GPU', 'Mantener - no sumar'),
  ('MRVL', 'Marvell',           'Networking',  'Consolidar'),
  ('QCOM', 'Qualcomm',          'Compute/IP',  'Consolidar')
) AS v(ticker, company, cluster, verdict) ON TRUE
WHERE a.ibkr_id = 'U22106929'
ON CONFLICT (account_id, ticker) DO NOTHING;

-- Cuenta Salud/Defensa (U26716079)
INSERT INTO holdings (account_id, ticker, company, cluster, verdict)
SELECT a.id, v.ticker, v.company, v.cluster, v.verdict
FROM accounts a
JOIN (VALUES
  ('LLY',   'Eli Lilly',   'Salud',   'Crecer'),
  ('ISRG',  'Intuitive Surgical', 'Salud', 'Mantener'),
  ('NVO',   'Novo Nordisk','Salud',   'Mantener'),
  ('ABBV',  'AbbVie',      'Salud',   'Mantener'),
  ('TMO',   'Thermo Fisher','Salud',  'Mantener'),
  ('HO.PA', 'Thales',      'Defensa', 'Objetivo (sin comprar)'),
  ('LDO.MI','Leonardo',    'Defensa', 'Objetivo (sin comprar)'),
  ('BA.L',  'BAE Systems', 'Defensa', 'Objetivo (sin comprar)'),
  ('RHM.DE','Rheinmetall', 'Defensa', 'Objetivo (sin comprar)')
) AS v(ticker, company, cluster, verdict) ON TRUE
WHERE a.ibkr_id = 'U26716079'
ON CONFLICT (account_id, ticker) DO NOTHING;

-- ── Config de triggers por ticker ────────────────────────────────────────────
-- Defaults §5: threshold -4.5%, ventana ~1 rueda (390 min).
-- Europeos (defensa) = fase 2 → enabled=false hasta activar cobertura EUR.
INSERT INTO ticker_config (ticker, enabled) VALUES
  ('NVDA', true), ('AVGO', true), ('ASML', true), ('ANET', true),
  ('TSM',  true), ('MSFT', true), ('VRT',  true), ('ETN',  true),
  ('ARM',  true), ('GEV',  true), ('GOOG', true), ('MU',   true),
  ('CEG',  true), ('AMD',  true), ('MRVL', true), ('QCOM', true),
  ('LLY',  true), ('ISRG', true), ('NVO',  true), ('ABBV', true),
  ('TMO',  true),
  ('HO.PA',  false), ('LDO.MI', false), ('BA.L', false), ('RHM.DE', false)
ON CONFLICT (ticker) DO NOTHING;
