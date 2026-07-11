# Portfolio Monitor & Alerting System

Sistema **read-only** que monitorea portfolios en Interactive Brokers, detecta
movimientos relevantes de precio, los cruza contra la tesis/veredicto de cada
activo y **avisa por Telegram** con una sugerencia contextualizada.

> 🔴 **Nunca ejecuta órdenes.** Solo observa y notifica. Human-in-the-loop siempre.
> El diseño completo está en [`CLAUDE.md`](CLAUDE.md).

## Estado

**Loop completo end-to-end.** Los 9 módulos del roadmap (`CLAUDE.md` §11) están
implementados y testeados: data layer (Finnhub → precios), gateway IBKR read-only
(sync de holdings), trigger engine + Verdict Gate + cooldown, fundamentals (FMP),
reasoning (Anthropic con fallback a template), notifier (Telegram), monitoreo
(dead-man's switch + status.json) y dashboards (Grafana provisionado sobre Postgres).

El `main.py` arranca el scheduler: cada tick pollea precios → detecta caídas →
razona → notifica por Telegram. Suite verde (83 tests). Pendientes: cablear el
sync de holdings de IBKR al loop (requiere gateway + 2FA) y los blindajes de §8
(digests + Trivy).

## Estructura

```
.
├── CLAUDE.md                 # Diseño / handoff (leer primero)
├── docker-compose.yml        # app · postgres(+timescale) · ib-gateway · kuma · grafana
├── .env.example              # Variables de entorno (copiar a .env)
├── db/
│   └── migrations/           # SQL, se corren en orden por docker-entrypoint-initdb.d
│       ├── 0001_extensions.sql
│       ├── 0002_schema.sql
│       ├── 0003_hypertables.sql
│       └── 0004_seed.sql
├── app/                      # Monolito modular (Python, non-root)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/portfolio_monitor/
│       ├── main.py           # entrypoint (stub)
│       ├── config.py
│       ├── db/               # acceso Postgres
│       ├── data/             # finnhub · edgar_fmp · ibkr (read-only)
│       ├── poller/           # price poller
│       ├── trigger/          # trigger engine + verdict gate
│       ├── reasoning/        # cliente Anthropic
│       ├── notifier/         # bot Telegram
│       ├── scheduler/        # orquestación de loops
│       └── monitoring/       # dead-man's switch + status.json
└── dashboards/grafana/       # provisioning (vacío)
```

## Arranque local

```bash
cp .env.example .env && chmod 600 .env   # rellenar secretos
docker compose up -d postgres            # aplica migraciones en volumen vacío
docker compose up -d                     # levanta el resto
# analítica opcional (Metabase):
docker compose --profile analytics up -d
```

UIs locales (bindeadas a `127.0.0.1`): Grafana `:3000`, Uptime Kuma `:3001`,
Metabase `:3002`. Postgres y el IB Gateway **no** se exponen al host.

## Tests

Requiere Python 3.12+ (el código usa `datetime.UTC`).

```bash
cd app
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest              # unit tests (sin infra)
ruff check .        # lint
```

Los tests de `tests/test_repositories.py` son de **integración**: se saltan solos
si no hay Postgres alcanzable. Para correrlos contra una DB real, apuntá
`DATABASE_URL` a una instancia con las migraciones aplicadas.
