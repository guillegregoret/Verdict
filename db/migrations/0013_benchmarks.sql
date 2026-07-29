-- ─────────────────────────────────────────────────────────────────────────────
-- 0013 — Benchmarks de mercado (amplitud / "¿es solo una bajada?")
--
-- Índices y ETFs sectoriales que el poller trae junto con los holdings para poder
-- responder si un movimiento es idiosincrático o de mercado. NO son posiciones:
-- no tienen veredicto ni entran al Verdict Gate (el trigger los ignora porque no
-- hay holding), solo alimentan el "contexto de mercado" que ve el reasoner.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS benchmarks (
    ticker  TEXT PRIMARY KEY,
    label   TEXT NOT NULL,                    -- nombre legible ("S&P 500")
    kind    TEXT NOT NULL DEFAULT 'index',    -- 'index' | 'sector'
    enabled BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO benchmarks (ticker, label, kind) VALUES
    ('SPY', 'S&P 500',        'index'),
    ('QQQ', 'Nasdaq 100',     'index'),
    ('SMH', 'Semiconductors', 'sector')
ON CONFLICT (ticker) DO NOTHING;
