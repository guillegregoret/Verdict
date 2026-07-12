# Portfolio Monitor & Alerting System

Sistema **read-only** que monitorea portfolios en Interactive Brokers, detecta
movimientos relevantes de precio, los cruza contra la tesis/veredicto de cada
activo y **avisa por Telegram** con una sugerencia contextualizada.

> 🔴 **Nunca ejecuta órdenes.** Solo observa y notifica. Human-in-the-loop siempre.
> El diseño completo está en [`CLAUDE.md`](CLAUDE.md).

## Estado

**Loop completo end-to-end.** Los 9 módulos del roadmap (`CLAUDE.md` §11) están
implementados y testeados: data layer (Finnhub → precios), gateway IBKR read-only
(sync de holdings cableado al scheduler), trigger engine + Verdict Gate + cooldown,
fundamentals (FMP), reasoning (Anthropic con fallback a template), notifier
(Telegram), monitoreo (Uptime Kuma auto-provisionado + dead-man's switch push) y
dashboards (Grafana provisionado + Metabase/Superset auto-inicializados sobre las
vistas de analítica). Pendientes: 2FA del gateway (login IBKR) y los blindajes
de §8 (digests + Trivy).

Stack pineado a últimas versiones: Postgres 17 + TimescaleDB 2.28, Python 3.14,
Grafana 13, Uptime Kuma 2.4, Metabase 0.62, Superset 6.1.

## Estructura

```
.
├── CLAUDE.md                 # Diseño / handoff (leer primero)
├── docker-compose.yml        # app · postgres · ib-gateway · kuma(+init) · grafana
│                             # · metabase(+init) · superset(+init) [perfil analytics]
├── .env.example              # Variables de entorno (copiar a .env)
├── db/
│   └── migrations/           # se corren en orden por docker-entrypoint-initdb.d
│       ├── 0001_extensions.sql
│       ├── 0002_schema.sql
│       ├── 0003_hypertables.sql
│       ├── 0004_seed.sql
│       ├── 0005_analytics_views.sql   # vistas para Grafana/Metabase/Superset
│       └── 0006_analytics_init.sh     # rol read-only + DBs de metadata
├── app/                      # Monolito modular (Python, non-root)
│   └── src/portfolio_monitor/
│       ├── main.py           # entrypoint: scheduler con el loop completo
│       ├── config.py
│       ├── db/               # acceso Postgres
│       ├── data/             # finnhub · edgar_fmp · ibkr (read-only)
│       ├── poller/           # price poller
│       ├── holdings/         # sync de posiciones IBKR (best-effort)
│       ├── trigger/          # trigger engine + verdict gate
│       ├── reasoning/        # cliente Anthropic
│       ├── notifier/         # bot Telegram
│       ├── scheduler/        # orquestación de loops
│       └── monitoring/       # dead-man's switch (push a Kuma) + status.json
├── monitoring/kuma/          # kuma-init.js: admin + monitores automáticos
└── dashboards/
    ├── grafana/              # provisioning (datasource read-only + dashboard)
    ├── metabase/             # metabase_init.py: conexión RO + dashboard
    └── superset/             # superset_config.py + init + datasets/charts
```

## Arranque local

```bash
cp .env.example .env && chmod 600 .env   # rellenar secretos
# COMPOSE_PROFILES=analytics en .env = booleano que activa Metabase + Superset
docker compose up -d --build
```

Los one-shots (`kuma-init`, `metabase-init`, `superset-init`) corren en cada
`up`, son idempotentes y dejan todo provisionado: monitores de Kuma apuntando a
gateway/postgres/app/APIs externas, y dashboards "Verdict — Portfolio" en
Metabase y Superset leyendo con el usuario **read-only** `verdict_ro`.

UIs locales (bindeadas a `127.0.0.1`): Grafana `:3000`, Uptime Kuma `:3001`,
Metabase `:3002`, Superset `:8088`. Postgres y el IB Gateway **no** se exponen
al host.

## Tests

Sin entorno local: los tests corren dentro de un contenedor efímero con la
imagen de la app (Python 3.14).

```bash
docker compose build app
docker run --rm -v "$PWD/app":/work -w /work portfolio-monitor-app \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -p no:cacheprovider && python -m ruff check ."
```

Los tests de `tests/test_repositories.py` son de **integración**: se saltan solos
si no hay Postgres alcanzable. Para correrlos contra una DB real, apuntá
`DATABASE_URL` a una instancia con las migraciones aplicadas.
