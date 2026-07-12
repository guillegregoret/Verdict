"""superset-assets — Provisioning REST de Superset (one-shot, idempotente).

Corre dentro del contenedor superset-init contra http://superset:8088. Solo
stdlib (urllib + cookiejar): login JWT + baile de CSRF, y luego:

  1. Conexión "Verdict (read-only)" a la DB del proyecto (usuario verdict_ro).
  2. Un dataset físico por cada vista de analítica (migración 0005).
  3. Charts (pie/bar/línea/tablas) + dashboard "Verdict — Portfolio" con layout.

Si el dashboard ya existe, sale sin tocar nada.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER = os.environ["SUPERSET_ADMIN_USER"]
ADMIN_PASSWORD = os.environ["SUPERSET_ADMIN_PASSWORD"]
PG_DB = os.environ["POSTGRES_DB"]
PG_RO_USER = os.environ["POSTGRES_RO_USER"]
PG_RO_PASSWORD = os.environ["POSTGRES_RO_PASSWORD"]

DB_NAME = "Verdict (read-only)"
DASHBOARD_TITLE = "Verdict — Portfolio"
HEALTH_TIMEOUT_S = 300

VIEWS = [
    "v_holdings_overview",
    "v_portfolio_by_cluster",
    "v_verdict_distribution",
    "v_price_daily",
    "v_drop_vs_window",
    "v_alerts_recent",
    "v_source_health_latest",
]

_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)
_token: str | None = None
_csrf: str | None = None


def log(msg: str) -> None:
    print(f"superset-assets: {msg}", flush=True)


def request(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Referer", BASE)
    if _token:
        req.add_header("Authorization", f"Bearer {_token}")
    if _csrf and method != "GET":
        req.add_header("X-CSRFToken", _csrf)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with _opener.open(req, data=data, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {detail}") from e
    return json.loads(raw) if raw else {}


def wait_health() -> None:
    log(f"esperando {BASE}/health ...")
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with _opener.open(f"{BASE}/health", timeout=10) as resp:
                if resp.status == 200:
                    log("Superset sano.")
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(5)
    raise RuntimeError("Superset no levantó a tiempo.")


def login() -> None:
    global _token, _csrf
    res = request("POST", "/api/v1/security/login", {
        "username": ADMIN_USER,
        "password": ADMIN_PASSWORD,
        "provider": "db",
        "refresh": True,
    })
    _token = res["access_token"]
    _csrf = request("GET", "/api/v1/security/csrf_token/")["result"]
    log("login OK.")


def list_all(path: str, name_key: str) -> dict[str, dict]:
    """Devuelve {nombre: objeto} paginando la lista del recurso."""
    out: dict[str, dict] = {}
    page = 0
    while True:
        q = urllib.parse.quote(f"(page:{page},page_size:100)")
        res = request("GET", f"{path}?q={q}")
        results = res.get("result", [])
        for item in results:
            out[item[name_key]] = item
        if len(results) < 100:
            return out
        page += 1


def ensure_database() -> int:
    existing = list_all("/api/v1/database/", "database_name")
    if DB_NAME in existing:
        log(f'conexión "{DB_NAME}" ya existe (id {existing[DB_NAME]["id"]}).')
        return existing[DB_NAME]["id"]
    log(f'creando conexión "{DB_NAME}" (usuario {PG_RO_USER}) ...')
    uri = (f"postgresql+psycopg2://{PG_RO_USER}:{urllib.parse.quote(PG_RO_PASSWORD)}"
           f"@postgres:5432/{PG_DB}")
    res = request("POST", "/api/v1/database/", {
        "database_name": DB_NAME,
        "sqlalchemy_uri": uri,
        "expose_in_sqllab": True,
    })
    return res["id"]


def ensure_datasets(db_id: int) -> dict[str, int]:
    existing = list_all("/api/v1/dataset/", "table_name")
    ids: dict[str, int] = {}
    for view in VIEWS:
        if view in existing:
            ids[view] = existing[view]["id"]
            log(f"dataset {view} ya existe (id {ids[view]}).")
            continue
        res = request("POST", "/api/v1/dataset/", {
            "database": db_id,
            "schema": "public",
            "table_name": view,
        })
        ids[view] = res["id"]
        log(f"dataset {view} creado (id {ids[view]}).")
    return ids


def _sum(col: str, label: str) -> dict:
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": col},
        "aggregate": "SUM",
        "label": label,
    }


def charts_wanted(ds: dict[str, int]) -> list[tuple[str, str, int, dict]]:
    """(nombre, viz_type, dataset_id, params) por chart."""
    return [
        ("Valor de mercado por cluster", "pie", ds["v_portfolio_by_cluster"], {
            "viz_type": "pie",
            "groupby": ["cluster"],
            "metric": _sum("valor_mercado", "Valor de mercado"),
            "adhoc_filters": [],
            "row_limit": 100,
        }),
        ("Distribución de veredictos", "pie", ds["v_verdict_distribution"], {
            "viz_type": "pie",
            "groupby": ["verdict"],
            "metric": _sum("valor_mercado", "Valor de mercado"),
            "adhoc_filters": [],
            "row_limit": 100,
        }),
        ("P/L no realizado por ticker", "echarts_timeseries_bar",
         ds["v_holdings_overview"], {
             "viz_type": "echarts_timeseries_bar",
             "x_axis": "ticker",
             "x_axis_sort_asc": True,
             "metrics": [_sum("pl_no_realizado", "P/L no realizado")],
             "adhoc_filters": [],
             "row_limit": 100,
         }),
        ("Precio de cierre diario", "echarts_timeseries_line",
         ds["v_price_daily"], {
             "viz_type": "echarts_timeseries_line",
             "x_axis": "dia",
             "time_grain_sqla": "P1D",
             "metrics": [{
                 "expressionType": "SIMPLE",
                 "column": {"column_name": "cierre"},
                 "aggregate": "AVG",
                 "label": "Cierre",
             }],
             "groupby": ["ticker"],
             "adhoc_filters": [],
             "row_limit": 10000,
         }),
        ("Holdings valorizados", "table", ds["v_holdings_overview"], {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": ["cuenta", "ticker", "company", "cluster", "verdict",
                            "shares", "avg_cost", "ultimo_precio", "costo_total",
                            "valor_mercado", "pl_no_realizado", "pl_pct"],
            "order_by_cols": [],
            "adhoc_filters": [],
            "row_limit": 200,
        }),
        ("Caída vs ventana (réplica del trigger)", "table",
         ds["v_drop_vs_window"], {
             "viz_type": "table",
             "query_mode": "raw",
             "all_columns": ["ticker", "verdict", "umbral_pct", "ventana_min",
                             "referencia", "actual", "pct_change", "bajo_umbral"],
             "order_by_cols": [],
             "adhoc_filters": [],
             "row_limit": 200,
         }),
        ("Alertas recientes", "table", ds["v_alerts_recent"], {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": ["ts", "ticker", "trigger_type", "pct_change",
                            "verdict", "suggestion", "status"],
            "order_by_cols": [],
            "adhoc_filters": [],
            "row_limit": 50,
        }),
        ("Salud de fuentes de datos", "table", ds["v_source_health_latest"], {
            "viz_type": "table",
            "query_mode": "raw",
            "all_columns": ["source", "ts", "status", "latency_ms"],
            "order_by_cols": [],
            "adhoc_filters": [],
            "row_limit": 50,
        }),
    ]


def build_position_json(chart_ids: list[tuple[int, str]]) -> dict:
    """Layout v2: filas de a 2 charts (grilla de 12 columnas)."""
    pos: dict = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID",
                      "meta": {"text": DASHBOARD_TITLE}},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [],
                    "parents": ["ROOT_ID"]},
    }
    for i in range(0, len(chart_ids), 2):
        row_id = f"ROW-{i // 2 + 1}"
        pos["GRID_ID"]["children"].append(row_id)
        row_children = []
        for chart_id, name in chart_ids[i:i + 2]:
            comp_id = f"CHART-{chart_id}"
            row_children.append(comp_id)
            pos[comp_id] = {
                "type": "CHART", "id": comp_id, "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {"chartId": chart_id, "width": 6, "height": 50,
                         "sliceName": name},
            }
        pos[row_id] = {
            "type": "ROW", "id": row_id, "children": row_children,
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
    return pos


def main() -> None:
    wait_health()
    login()

    dashboards = list_all("/api/v1/dashboard/", "dashboard_title")
    if DASHBOARD_TITLE in dashboards:
        log(f'dashboard "{DASHBOARD_TITLE}" ya existe → nada que hacer.')
        return

    db_id = ensure_database()
    ds = ensure_datasets(db_id)

    dash = request("POST", "/api/v1/dashboard/", {
        "dashboard_title": DASHBOARD_TITLE,
        "published": True,
    })
    dash_id = dash["id"]
    log(f'dashboard "{DASHBOARD_TITLE}" creado (id {dash_id}).')

    existing_charts = list_all("/api/v1/chart/", "slice_name")
    chart_ids: list[tuple[int, str]] = []
    for name, viz, dataset_id, params in charts_wanted(ds):
        if name in existing_charts:
            chart_ids.append((existing_charts[name]["id"], name))
            log(f'chart "{name}" ya existe.')
            continue
        res = request("POST", "/api/v1/chart/", {
            "slice_name": name,
            "viz_type": viz,
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "params": json.dumps(params),
            "dashboards": [dash_id],
        })
        chart_ids.append((res["id"], name))
        log(f'chart "{name}" creado (id {res["id"]}).')

    request("PUT", f"/api/v1/dashboard/{dash_id}", {
        "position_json": json.dumps(build_position_json(chart_ids)),
    })
    log("layout aplicado. listo.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — one-shot: loguear y salir con error
        log(f"ERROR — {exc}")
        sys.exit(1)
