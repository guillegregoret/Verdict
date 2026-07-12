"""metabase-init — Provisioning headless de Metabase (one-shot, idempotente).

Solo stdlib (urllib) para correr en python:slim sin pip ni egress. Flujo:

  1. Espera /api/health (el primer boot migra su metadata y tarda minutos).
  2. Si la instancia está virgen usa el setup-token para crear el admin;
     si no, hace login con las credenciales de .env.
  3. Crea la conexión "Verdict (read-only)" a la DB del proyecto usando el
     usuario de SOLO LECTURA (verdict_ro) y espera a que sincronice las vistas.
  4. Crea las cards (SQL nativo sobre las vistas de 0005) y el dashboard
     "Verdict — Portfolio". Si el dashboard ya existe, no toca nada.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("METABASE_URL", "http://metabase:3000")
ADMIN_EMAIL = os.environ["METABASE_ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["METABASE_ADMIN_PASSWORD"]
PG_DB = os.environ["POSTGRES_DB"]
PG_RO_USER = os.environ["POSTGRES_RO_USER"]
PG_RO_PASSWORD = os.environ["POSTGRES_RO_PASSWORD"]

DB_NAME = "Verdict (read-only)"
DASHBOARD_NAME = "Verdict — Portfolio"
HEALTH_TIMEOUT_S = 600   # primer boot con migraciones: puede tardar varios minutos
SYNC_TIMEOUT_S = 300


def log(msg: str) -> None:
    print(f"metabase-init: {msg}", flush=True)


def request(method: str, path: str, body: dict | None = None,
            session: str | None = None) -> dict | list:
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    if session:
        req.add_header("X-Metabase-Session", session)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {detail}") from e
    return json.loads(raw) if raw else {}


def wait_health() -> None:
    log(f"esperando {BASE}/api/health ...")
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            res = request("GET", "/api/health")
            if isinstance(res, dict) and res.get("status") == "ok":
                log("Metabase sano.")
                return
        except (urllib.error.URLError, RuntimeError, OSError):
            pass
        time.sleep(5)
    raise RuntimeError("Metabase no levantó a tiempo.")


def get_session() -> str:
    props = request("GET", "/api/session/properties")
    token = props.get("setup-token") if isinstance(props, dict) else None
    if token:
        log("instancia virgen → creando admin ...")
        res = request("POST", "/api/setup", {
            "token": token,
            "user": {
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "first_name": "Verdict",
                "last_name": "Admin",
                "site_name": "Verdict",
            },
            "prefs": {"site_name": "Verdict", "allow_tracking": "false"},
        })
        return res["id"]
    log("setup ya hecho → login ...")
    res = request("POST", "/api/session",
                  {"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    return res["id"]


def ensure_database(session: str) -> int:
    dbs = request("GET", "/api/database", session=session)
    items = dbs["data"] if isinstance(dbs, dict) else dbs
    for db in items:
        if db["name"] == DB_NAME:
            log(f'conexión "{DB_NAME}" ya existe (id {db["id"]}).')
            return db["id"]
    log(f'creando conexión "{DB_NAME}" (usuario {PG_RO_USER}) ...')
    res = request("POST", "/api/database", {
        "engine": "postgres",
        "name": DB_NAME,
        "details": {
            "host": "postgres",
            "port": 5432,
            "dbname": PG_DB,
            "user": PG_RO_USER,
            "password": PG_RO_PASSWORD,
            "ssl": False,
        },
        "is_full_sync": True,
    }, session=session)
    return res["id"]


def wait_views(session: str, db_id: int) -> None:
    """Espera a que el sync de schema exponga las vistas de analítica."""
    request("POST", f"/api/database/{db_id}/sync_schema", {}, session=session)
    log("esperando el sync del schema ...")
    deadline = time.monotonic() + SYNC_TIMEOUT_S
    while time.monotonic() < deadline:
        meta = request("GET", f"/api/database/{db_id}/metadata", session=session)
        names = {t["name"] for t in meta.get("tables", [])}
        if "v_holdings_overview" in names:
            log(f"schema sincronizado ({len(names)} tablas/vistas).")
            return
        time.sleep(5)
    raise RuntimeError("el sync del schema no expuso las vistas a tiempo.")


def dashboard_exists(session: str) -> bool:
    res = request("GET", "/api/search?q=Verdict&models=dashboard", session=session)
    items = res.get("data", []) if isinstance(res, dict) else res
    return any(item.get("name") == DASHBOARD_NAME for item in items)


# (nombre, display, sql, visualization_settings, size_x, size_y)
CARDS = [
    ("Holdings valorizados", "table",
     "SELECT cuenta, ticker, company, cluster, verdict, shares, avg_cost, "
     "ultimo_precio, costo_total, valor_mercado, pl_no_realizado, pl_pct "
     "FROM v_holdings_overview ORDER BY valor_mercado DESC NULLS LAST",
     {}, 24, 8),
    ("P/L no realizado por ticker", "bar",
     "SELECT ticker, pl_no_realizado FROM v_holdings_overview "
     "WHERE pl_no_realizado IS NOT NULL ORDER BY pl_no_realizado",
     {"graph.dimensions": ["ticker"], "graph.metrics": ["pl_no_realizado"]},
     12, 8),
    ("Valor de mercado por cluster", "pie",
     "SELECT cluster, valor_mercado FROM v_portfolio_by_cluster",
     {"pie.dimension": "cluster", "pie.metric": "valor_mercado"}, 12, 8),
    ("Distribución de veredictos", "pie",
     "SELECT verdict, valor_mercado FROM v_verdict_distribution",
     {"pie.dimension": "verdict", "pie.metric": "valor_mercado"}, 12, 8),
    ("Precio de cierre diario", "line",
     "SELECT dia, ticker, cierre FROM v_price_daily ORDER BY dia",
     {"graph.dimensions": ["dia", "ticker"], "graph.metrics": ["cierre"]},
     12, 8),
    ("Caída vs ventana (réplica del trigger)", "table",
     "SELECT ticker, verdict, umbral_pct, ventana_min, referencia, actual, "
     "pct_change, bajo_umbral FROM v_drop_vs_window ORDER BY pct_change",
     {}, 12, 8),
    ("Alertas recientes", "table",
     "SELECT ts, ticker, trigger_type, pct_change, verdict, suggestion, status "
     "FROM v_alerts_recent LIMIT 50",
     {}, 12, 8),
    ("Salud de fuentes de datos", "table",
     "SELECT source, ts, status, latency_ms FROM v_source_health_latest",
     {}, 24, 4),
]


def create_card(session: str, db_id: int, name: str, display: str,
                sql: str, viz: dict) -> int:
    res = request("POST", "/api/card", {
        "name": name,
        "display": display,
        "collection_id": None,
        "dataset_query": {
            "type": "native",
            "native": {"query": sql, "template-tags": {}},
            "database": db_id,
        },
        "visualization_settings": viz,
    }, session=session)
    return res["id"]


def build_dashboard(session: str, db_id: int) -> None:
    log("creando cards ...")
    dashcards = []
    row = col = 0
    for i, (name, display, sql, viz, sx, sy) in enumerate(CARDS):
        card_id = create_card(session, db_id, name, display, sql, viz)
        if col + sx > 24:            # grilla de 24 columnas
            col = 0
            row += 8
        dashcards.append({
            "id": -(i + 1),          # ids negativos = dashcards nuevas
            "card_id": card_id,
            "row": row, "col": col, "size_x": sx, "size_y": sy,
            "series": [], "parameter_mappings": [],
            "visualization_settings": {},
        })
        col += sx
        log(f'  card "{name}" (id {card_id}).')

    dash = request("POST", "/api/dashboard", {"name": DASHBOARD_NAME},
                   session=session)
    request("PUT", f"/api/dashboard/{dash['id']}",
            {"dashcards": dashcards}, session=session)
    log(f'dashboard "{DASHBOARD_NAME}" creado (id {dash["id"]}).')


def main() -> None:
    wait_health()
    session = get_session()
    db_id = ensure_database(session)
    if dashboard_exists(session):
        log(f'dashboard "{DASHBOARD_NAME}" ya existe → nada que hacer.')
        return
    wait_views(session, db_id)
    build_dashboard(session, db_id)
    log("listo.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — one-shot: loguear y salir con error
        log(f"ERROR — {exc}")
        sys.exit(1)
