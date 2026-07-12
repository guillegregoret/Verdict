-- ─────────────────────────────────────────────────────────────────────────────
-- 0005 — Vistas de analítica (§10)
-- Métricas listas para consumir por Grafana / Metabase / Superset, sin duplicar
-- SQL en cada herramienta. Todas READ-ONLY sobre las tablas base.
-- ─────────────────────────────────────────────────────────────────────────────

-- Último precio conocido por ticker.
CREATE OR REPLACE VIEW v_latest_price AS
SELECT DISTINCT ON (ticker)
    ticker,
    ts    AS price_ts,
    price
FROM prices
ORDER BY ticker, ts DESC;

-- Réplica de la evaluación del Trigger Engine: caída actual vs ventana por ticker.
CREATE OR REPLACE VIEW v_drop_vs_window AS
SELECT
    tc.ticker,
    h.verdict,
    tc.threshold_pct                                            AS umbral_pct,
    tc.window_minutes                                           AS ventana_min,
    ref.price                                                   AS referencia,
    cur.price                                                   AS actual,
    round((cur.price - ref.price) / NULLIF(ref.price, 0) * 100, 2) AS pct_change,
    (cur.price - ref.price) / NULLIF(ref.price, 0) * 100
        <= tc.threshold_pct                                     AS bajo_umbral
FROM ticker_config tc
LEFT JOIN LATERAL (
    SELECT price FROM prices p
    WHERE p.ticker = tc.ticker
      AND p.ts >= now() - (tc.window_minutes || ' minutes')::interval
    ORDER BY p.ts ASC LIMIT 1
) ref ON true
LEFT JOIN LATERAL (
    SELECT price FROM prices p
    WHERE p.ticker = tc.ticker
    ORDER BY p.ts DESC LIMIT 1
) cur ON true
LEFT JOIN holdings h ON h.ticker = tc.ticker
WHERE tc.enabled;

-- Holdings valorizados: cantidad, costo, valor de mercado y P/L no realizado.
CREATE OR REPLACE VIEW v_holdings_overview AS
SELECT
    a.name                                        AS cuenta,
    a.ibkr_id,
    h.ticker,
    h.company,
    h.cluster,
    h.verdict,
    h.shares,
    h.avg_cost,
    lp.price                                      AS ultimo_precio,
    round(h.shares * h.avg_cost, 2)               AS costo_total,
    round(h.shares * lp.price, 2)                 AS valor_mercado,
    round(h.shares * (lp.price - h.avg_cost), 2)  AS pl_no_realizado,
    round((lp.price - h.avg_cost) / NULLIF(h.avg_cost, 0) * 100, 2) AS pl_pct,
    h.updated_at
FROM holdings h
JOIN accounts a        ON a.id = h.account_id
LEFT JOIN v_latest_price lp ON lp.ticker = h.ticker;

-- Valor de mercado agrupado por cluster (para tortas/Sankey).
CREATE OR REPLACE VIEW v_portfolio_by_cluster AS
SELECT
    COALESCE(cluster, 'Sin cluster') AS cluster,
    count(*)                         AS posiciones,
    round(sum(valor_mercado), 2)     AS valor_mercado
FROM v_holdings_overview
GROUP BY 1;

-- Distribución de veredictos (cantidad y valor).
CREATE OR REPLACE VIEW v_verdict_distribution AS
SELECT
    verdict,
    count(*)                     AS posiciones,
    round(sum(valor_mercado), 2) AS valor_mercado
FROM v_holdings_overview
GROUP BY 1;

-- Serie diaria por ticker (apertura/cierre/min/max del día) para gráficos.
CREATE OR REPLACE VIEW v_price_daily AS
SELECT
    ticker,
    time_bucket('1 day', ts)                            AS dia,
    first(price, ts)                                    AS apertura,
    last(price, ts)                                     AS cierre,
    min(price)                                          AS minimo,
    max(price)                                          AS maximo,
    count(*)                                            AS muestras
FROM prices
GROUP BY ticker, time_bucket('1 day', ts);

-- Alertas recientes (auditoría legible).
CREATE OR REPLACE VIEW v_alerts_recent AS
SELECT
    al.ts,
    al.ticker,
    al.trigger_type,
    al.pct_change,
    al.window_minutes,
    al.verdict,
    al.suggestion,
    al.status,
    al.sent_via
FROM alerts al
ORDER BY al.ts DESC;

-- Último estado reportado por cada fuente de datos.
CREATE OR REPLACE VIEW v_source_health_latest AS
SELECT DISTINCT ON (source)
    source,
    ts,
    status,
    latency_ms
FROM data_source_health
ORDER BY source, ts DESC;
