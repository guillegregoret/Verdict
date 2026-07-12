"""Config de Superset para Verdict (montada en /app/pythonpath/).

La metadata de Superset vive en la base `superset_meta` (creada por la
migración 0006), separada de la data del portfolio. La conexión a la data
del proyecto se provisiona aparte (superset_assets.py) con el usuario
read-only `verdict_ro`.
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_META_URI"]

# La UI se sirve solo en 127.0.0.1 detrás del bind del compose; Talisman
# fuerza HTTPS/CSP y rompe el acceso local por http.
TALISMAN_ENABLED = False

# Sin telemetría ni features experimentales.
FEATURE_FLAGS: dict[str, bool] = {}
